"""SOP Conflict Center endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select

from app.deps import CurrentUser, DbSession, Pagination, record_event, require_reviewer
from app.models.entities import ReviewAction, SopConflict, SopDocument, User
from app.models.enums import SEVERITY_ORDER, ConflictStatus
from app.schemas import ConflictOut, ConflictResolveIn, Page, ReviewActionOut
from app.services.audit.conflicts import resolve_conflict

router = APIRouter(prefix="/conflicts", tags=["conflicts"])


def _severity_rank():
    """Rank by operational severity - alphabetical ordering would put 'info' above 'major'."""
    return case(
        {str(level): rank for level, rank in SEVERITY_ORDER.items()},
        value=SopConflict.severity,
        else_=len(SEVERITY_ORDER),
    )


def _out(db: DbSession, conflict: SopConflict) -> ConflictOut:
    out = ConflictOut.model_validate(conflict)
    if conflict.document_id:
        document = db.get(SopDocument, conflict.document_id)
        out.sop_code = document.sop_code if document else None
    # The detector already stored a readable label and revision code in each evidence
    # blob; surface them so clients never have to re-query revisions by id.
    out.revision_a_code = (conflict.evidence_a or {}).get("revision_code")
    out.revision_b_code = (conflict.evidence_b or {}).get("revision_code")
    if conflict.resolved_by_id:
        resolver = db.get(User, conflict.resolved_by_id)
        out.resolved_by = resolver.username if resolver else None
    return out


@router.get("", response_model=Page[ConflictOut])
def list_conflicts(
    db: DbSession,
    user: CurrentUser,
    pagination: Pagination,
    status_filter: str | None = Query(default=None, alias="status"),
    conflict_type: str | None = None,
    severity: str | None = None,
    document_id: int | None = None,
) -> Page[ConflictOut]:
    stmt = select(SopConflict)
    if status_filter:
        # Accept a comma-separated list so a client can ask for everything that still
        # needs attention ("open,escalated") in one call.
        wanted = [part.strip() for part in status_filter.split(",") if part.strip()]
        if wanted:
            stmt = stmt.where(SopConflict.status.in_(wanted))
    if conflict_type:
        stmt = stmt.where(SopConflict.conflict_type == conflict_type)
    if severity:
        stmt = stmt.where(SopConflict.severity == severity)
    if document_id:
        stmt = stmt.where(SopConflict.document_id == document_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(_severity_rank(), SopConflict.id.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()
    return Page[ConflictOut](
        items=[_out(db, c) for c in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=max(1, -(-total // pagination.page_size)),
    )


@router.get("/{conflict_id}", response_model=ConflictOut)
def get_conflict(conflict_id: int, db: DbSession, user: CurrentUser) -> ConflictOut:
    conflict = db.get(SopConflict, conflict_id)
    if conflict is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conflict not found.")
    return _out(db, conflict)


@router.post("/{conflict_id}/resolve", response_model=ConflictOut)
def resolve(
    conflict_id: int,
    payload: ConflictResolveIn,
    db: DbSession,
    actor: User = Depends(require_reviewer),
) -> ConflictOut:
    conflict = db.get(SopConflict, conflict_id)
    if conflict is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conflict not found.")
    if conflict.status in {ConflictStatus.RESOLVED, ConflictStatus.ACCEPTED_RISK}:
        raise HTTPException(status.HTTP_409_CONFLICT, "This conflict has already been resolved.")
    previous = conflict.status
    try:
        resolve_conflict(
            db,
            conflict,
            choice=payload.choice,
            rationale=payload.rationale,
            actor_id=actor.id,
            accept_risk=payload.accept_risk,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    db.add(
        ReviewAction(
            entity_type="conflict",
            entity_id=conflict.id,
            action="resolve_conflict",
            from_state=previous,
            to_state=conflict.status,
            reason=payload.rationale,
            actor_id=actor.id,
            actor_name=actor.username,
            payload={"choice": payload.choice, "accept_risk": payload.accept_risk},
        )
    )
    record_event(
        db,
        event_type="conflict_resolved",
        actor=actor,
        entity_type="conflict",
        entity_id=conflict.id,
        message=f"{actor.username} resolved {conflict.conflict_code} as '{payload.choice}'",
    )
    db.flush()
    return _out(db, conflict)


@router.get("/{conflict_id}/history", response_model=list[ReviewActionOut])
def conflict_history(conflict_id: int, db: DbSession, user: CurrentUser) -> list[ReviewActionOut]:
    rows = db.scalars(
        select(ReviewAction)
        .where(ReviewAction.entity_type == "conflict", ReviewAction.entity_id == conflict_id)
        .order_by(ReviewAction.created_at.desc())
    )
    return [ReviewActionOut.model_validate(r) for r in rows]
