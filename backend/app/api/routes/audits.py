"""Audit execution, findings and the human review workflow."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, or_, select

from app.deps import CurrentUser, DbSession, Pagination, record_event, require_auditor, require_reviewer
from app.models.entities import (
    Audit,
    AuditFinding,
    Citation,
    ProcessReport,
    ReviewAction,
    SopRevision,
    User,
)
from app.models.enums import SEVERITY_ORDER, AuditOutcome, ReviewStatus
from app.schemas import (
    AuditCreate,
    AuditDetail,
    AuditOut,
    CitationOut,
    FindingOut,
    Page,
    ReviewActionIn,
    ReviewActionOut,
)
from app.services.audit.pipeline import AuditError, run_audit

router = APIRouter(prefix="/audits", tags=["audits"])


def _augment(db: DbSession, audit: Audit) -> AuditOut:
    out = AuditOut.model_validate(audit)
    report = db.get(ProcessReport, audit.report_id)
    out.report_code = report.report_code if report else None
    if audit.resolved_revision_id:
        revision = db.get(SopRevision, audit.resolved_revision_id)
        if revision:
            out.revision_code = revision.revision_code
            out.sop_code = revision.document.sop_code if revision.document else None
    elif report is not None:
        out.sop_code = report.declared_sop_code
    out.finding_count = (
        db.scalar(select(func.count(AuditFinding.id)).where(AuditFinding.audit_id == audit.id)) or 0
    )
    return out


@router.get("", response_model=Page[AuditOut])
def list_audits(
    db: DbSession,
    user: CurrentUser,
    pagination: Pagination,
    outcome: str | None = None,
    review_status: str | None = None,
    requires_review: bool | None = None,
    sop_code: str | None = None,
    search: str | None = None,
) -> Page[AuditOut]:
    stmt = select(Audit)
    if outcome:
        stmt = stmt.where(Audit.outcome == outcome)
    if review_status:
        stmt = stmt.where(Audit.review_status == review_status)
    if requires_review is not None:
        stmt = stmt.where(Audit.requires_human_review.is_(requires_review))
    if sop_code:
        stmt = stmt.where(
            Audit.report_id.in_(select(ProcessReport.id).where(ProcessReport.declared_sop_code == sop_code))
        )
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Audit.audit_code.ilike(pattern),
                Audit.report_id.in_(
                    select(ProcessReport.id).where(
                        or_(
                            ProcessReport.report_code.ilike(pattern),
                            ProcessReport.batch_id.ilike(pattern),
                        )
                    )
                ),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Audit.created_at.desc(), Audit.id.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()
    return Page[AuditOut](
        items=[_augment(db, a) for a in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=max(1, -(-total // pagination.page_size)),
    )


@router.post("", response_model=AuditDetail, status_code=201)
async def create_audit(
    payload: AuditCreate, db: DbSession, actor: User = Depends(require_auditor)
) -> AuditDetail:
    report = db.get(ProcessReport, payload.report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Process report not found.")
    try:
        audit = await run_audit(db, report, mode=payload.mode, actor_id=actor.id)
    except AuditError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Audit failed: {exc}") from exc
    db.flush()
    return _detail(db, audit)


def _detail(db: DbSession, audit: Audit) -> AuditDetail:
    base = _augment(db, audit)
    detail = AuditDetail(
        **base.model_dump(),
        version_resolution=audit.version_resolution,
        pipeline_log=audit.pipeline_log,
        input_snapshot=audit.input_snapshot,
        error_message=audit.error_message,
    )
    findings = db.scalars(
        select(AuditFinding).where(AuditFinding.audit_id == audit.id).order_by(AuditFinding.id)
    ).all()
    detail.findings = [_finding_out(db, f) for f in findings]
    return detail


def _finding_out(db: DbSession, finding: AuditFinding) -> FindingOut:
    out = FindingOut.model_validate(finding)
    out.citations = [
        CitationOut.model_validate(c)
        for c in db.scalars(select(Citation).where(Citation.finding_id == finding.id).order_by(Citation.id))
    ]
    return out


@router.get("/{audit_id}", response_model=AuditDetail)
def get_audit(audit_id: int, db: DbSession, user: CurrentUser) -> AuditDetail:
    audit = db.get(Audit, audit_id)
    if audit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audit not found.")
    return _detail(db, audit)


@router.get("/{audit_id}/history", response_model=list[ReviewActionOut])
def audit_history(audit_id: int, db: DbSession, user: CurrentUser) -> list[ReviewActionOut]:
    audit = db.get(Audit, audit_id)
    if audit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audit not found.")
    finding_ids = [
        row[0] for row in db.execute(select(AuditFinding.id).where(AuditFinding.audit_id == audit_id)).all()
    ]
    stmt = (
        select(ReviewAction)
        .where(
            or_(
                (ReviewAction.entity_type == "audit") & (ReviewAction.entity_id == audit_id),
                (ReviewAction.entity_type == "finding") & (ReviewAction.entity_id.in_(finding_ids or [-1])),
            )
        )
        .order_by(ReviewAction.created_at.desc())
    )
    return [ReviewActionOut.model_validate(r) for r in db.scalars(stmt)]


# --------------------------------------------------------------------------- #
# Review queue & actions
# --------------------------------------------------------------------------- #
findings_router = APIRouter(prefix="/findings", tags=["review"])


@findings_router.get("", response_model=Page[FindingOut])
def list_findings(
    db: DbSession,
    user: CurrentUser,
    pagination: Pagination,
    review_status: str | None = None,
    severity: str | None = None,
    classification: str | None = None,
    category: str | None = None,
    audit_id: int | None = None,
    verification_status: str | None = None,
    search: str | None = Query(default=None),
) -> Page[FindingOut]:
    stmt = select(AuditFinding)
    if review_status:
        stmt = stmt.where(AuditFinding.review_status == review_status)
    if severity:
        stmt = stmt.where(AuditFinding.severity == severity)
    if classification:
        stmt = stmt.where(AuditFinding.classification == classification)
    if category:
        stmt = stmt.where(AuditFinding.category == category)
    if audit_id:
        stmt = stmt.where(AuditFinding.audit_id == audit_id)
    if verification_status:
        stmt = stmt.where(AuditFinding.verification_status == verification_status)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                AuditFinding.title.ilike(pattern),
                AuditFinding.explanation.ilike(pattern),
                AuditFinding.requirement_key.ilike(pattern),
                AuditFinding.finding_code.ilike(pattern),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    # Rank by operational severity, not alphabetically ("critical" < "info" < "major" < "minor"
    # as raw strings would put informational findings above major ones).
    severity_rank = case(
        {str(level): rank for level, rank in SEVERITY_ORDER.items()},
        value=AuditFinding.severity,
        else_=len(SEVERITY_ORDER),
    )
    rows = db.scalars(
        stmt.order_by(severity_rank.asc(), AuditFinding.id.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()
    return Page[FindingOut](
        items=[_finding_out(db, f) for f in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=max(1, -(-total // pagination.page_size)),
    )


_ACTION_TO_STATUS = {
    "accept": ReviewStatus.ACCEPTED,
    "reject": ReviewStatus.REJECTED,
    "request_info": ReviewStatus.INFO_REQUESTED,
    "resolve": ReviewStatus.RESOLVED,
    "reopen": ReviewStatus.OPEN,
    "assign": ReviewStatus.IN_REVIEW,
}


@findings_router.post("/{finding_id}/review", response_model=FindingOut)
def review_finding(
    finding_id: int,
    payload: ReviewActionIn,
    db: DbSession,
    actor: User = Depends(require_reviewer),
) -> FindingOut:
    finding = db.get(AuditFinding, finding_id)
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Finding not found.")
    new_status = _ACTION_TO_STATUS[payload.action]
    previous = finding.review_status

    # The machine output is preserved; only the review envelope changes.
    finding.review_status = new_status
    finding.reviewer_id = actor.id
    finding.reviewed_at = datetime.now(UTC)
    finding.reviewer_comment = payload.reason

    db.add(
        ReviewAction(
            entity_type="finding",
            entity_id=finding.id,
            action=payload.action,
            from_state=previous,
            to_state=new_status,
            reason=payload.reason,
            actor_id=actor.id,
            actor_name=actor.username,
            payload={
                "finding_code": finding.finding_code,
                "classification_at_review": finding.classification,
                "severity": finding.severity,
            },
        )
    )
    db.flush()
    _refresh_audit_review_state(db, finding.audit_id)
    record_event(
        db,
        event_type="finding_reviewed",
        actor=actor,
        entity_type="finding",
        entity_id=finding.id,
        message=f"{actor.username} {payload.action} {finding.finding_code}",
    )
    return _finding_out(db, finding)


@router.post("/{audit_id}/review", response_model=AuditOut)
def review_audit(
    audit_id: int, payload: ReviewActionIn, db: DbSession, actor: User = Depends(require_reviewer)
) -> AuditOut:
    audit = db.get(Audit, audit_id)
    if audit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audit not found.")
    previous = audit.review_status
    if payload.action == "assign":
        if payload.assignee_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "assignee_id is required to assign.")
        if db.get(User, payload.assignee_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignee not found.")
        audit.assigned_reviewer_id = payload.assignee_id
        audit.review_status = ReviewStatus.IN_REVIEW
    else:
        audit.review_status = _ACTION_TO_STATUS[payload.action]
        audit.reviewed_at = datetime.now(UTC)

    db.add(
        ReviewAction(
            entity_type="audit",
            entity_id=audit.id,
            action=payload.action,
            from_state=previous,
            to_state=audit.review_status,
            reason=payload.reason,
            actor_id=actor.id,
            actor_name=actor.username,
            payload={"assignee_id": payload.assignee_id, "outcome": audit.outcome},
        )
    )
    record_event(
        db,
        event_type="audit_reviewed",
        actor=actor,
        entity_type="audit",
        entity_id=audit.id,
        message=f"{actor.username} {payload.action} audit {audit.audit_code}",
    )
    db.flush()
    return _augment(db, audit)


def _refresh_audit_review_state(db: DbSession, audit_id: int) -> None:
    """Derive the audit review state from its findings (never silently 'closes' an audit)."""
    audit = db.get(Audit, audit_id)
    if audit is None:
        return
    statuses = [
        row[0]
        for row in db.execute(
            select(AuditFinding.review_status).where(AuditFinding.audit_id == audit_id)
        ).all()
    ]
    if not statuses:
        return
    open_like = {ReviewStatus.OPEN, ReviewStatus.IN_REVIEW, ReviewStatus.INFO_REQUESTED}
    if all(s not in open_like for s in statuses):
        audit.review_status = ReviewStatus.RESOLVED
        audit.reviewed_at = datetime.now(UTC)
    elif any(s != ReviewStatus.OPEN for s in statuses):
        audit.review_status = ReviewStatus.IN_REVIEW
    db.flush()


@findings_router.get("/{finding_id}", response_model=FindingOut)
def get_finding(finding_id: int, db: DbSession, user: CurrentUser) -> FindingOut:
    finding = db.get(AuditFinding, finding_id)
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Finding not found.")
    return _finding_out(db, finding)


@findings_router.get("/{finding_id}/history", response_model=list[ReviewActionOut])
def finding_history(finding_id: int, db: DbSession, user: CurrentUser) -> list[ReviewActionOut]:
    rows = db.scalars(
        select(ReviewAction)
        .where(ReviewAction.entity_type == "finding", ReviewAction.entity_id == finding_id)
        .order_by(ReviewAction.created_at.desc())
    )
    return [ReviewActionOut.model_validate(r) for r in rows]


@findings_router.get("/queue/summary")
def review_queue_summary(db: DbSession, user: CurrentUser) -> dict:
    by_status = dict(
        db.execute(
            select(AuditFinding.review_status, func.count(AuditFinding.id)).group_by(
                AuditFinding.review_status
            )
        ).all()
    )
    by_severity = dict(
        db.execute(
            select(AuditFinding.severity, func.count(AuditFinding.id))
            .where(AuditFinding.review_status == ReviewStatus.OPEN)
            .group_by(AuditFinding.severity)
        ).all()
    )
    audits_waiting = (
        db.scalar(
            select(func.count(Audit.id)).where(
                Audit.requires_human_review.is_(True),
                Audit.review_status.in_([ReviewStatus.OPEN, ReviewStatus.IN_REVIEW]),
            )
        )
        or 0
    )
    blocked = (
        db.scalar(
            select(func.count(Audit.id)).where(Audit.outcome == AuditOutcome.BLOCKED_UNRESOLVED_AUTHORITY)
        )
        or 0
    )
    return {
        "findings_by_review_status": by_status,
        "open_findings_by_severity": by_severity,
        "audits_awaiting_review": audits_waiting,
        "audits_blocked_on_authority": blocked,
    }
