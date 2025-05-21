"""Process execution report endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select

from app.deps import CurrentUser, DbSession, Pagination, require_auditor
from app.models.entities import Audit, ProcessReport, ReportEvent, User
from app.schemas import (
    ManualReportIn,
    Page,
    ProcessReportDetail,
    ProcessReportOut,
    ReportEventOut,
)
from app.services.ingestion.storage import UploadValidationError
from app.services.reports import DuplicateReportError, ingest_manual_report, ingest_process_report

router = APIRouter(prefix="/reports", tags=["process-reports"])


def _augment(db: DbSession, report: ProcessReport) -> ProcessReportOut:
    out = ProcessReportOut.model_validate(report)
    out.event_count = (
        db.scalar(select(func.count(ReportEvent.id)).where(ReportEvent.report_id == report.id)) or 0
    )
    out.audit_count = db.scalar(select(func.count(Audit.id)).where(Audit.report_id == report.id)) or 0
    return out


@router.get("", response_model=Page[ProcessReportOut])
def list_reports(
    db: DbSession,
    user: CurrentUser,
    pagination: Pagination,
    search: str | None = None,
    sop_code: str | None = None,
    audited: bool | None = Query(default=None, description="Filter by whether an audit exists"),
) -> Page[ProcessReportOut]:
    stmt = select(ProcessReport)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                ProcessReport.report_code.ilike(pattern),
                ProcessReport.title.ilike(pattern),
                ProcessReport.batch_id.ilike(pattern),
                ProcessReport.operator.ilike(pattern),
                ProcessReport.product_code.ilike(pattern),
            )
        )
    if sop_code:
        stmt = stmt.where(ProcessReport.declared_sop_code == sop_code)
    if audited is not None:
        sub = select(Audit.report_id)
        stmt = stmt.where(ProcessReport.id.in_(sub) if audited else ProcessReport.id.not_in(sub))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(ProcessReport.created_at.desc()).offset(pagination.offset).limit(pagination.page_size)
    ).all()
    return Page[ProcessReportOut](
        items=[_augment(db, r) for r in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=max(1, -(-total // pagination.page_size)),
    )


@router.post("/upload", response_model=ProcessReportOut, status_code=201)
async def upload_report(
    db: DbSession,
    file: Annotated[UploadFile, File()],
    actor: Annotated[User, Depends(require_auditor)],
    declared_sop_code: Annotated[str | None, Form()] = None,
    declared_revision_code: Annotated[str | None, Form()] = None,
    executed_at: Annotated[str | None, Form()] = None,
    allow_duplicate: Annotated[bool, Form()] = False,
) -> ProcessReportOut:
    data = await file.read()
    overrides = {
        k: v
        for k, v in {
            "declared_sop_code": declared_sop_code or None,
            "declared_revision_code": declared_revision_code or None,
            "executed_at": executed_at or None,
        }.items()
        if v is not None
    }
    try:
        report = ingest_process_report(
            db,
            data=data,
            filename=file.filename or "report",
            uploaded_by_id=actor.id,
            overrides=overrides,
            allow_duplicate=allow_duplicate,
        )
    except DuplicateReportError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except UploadValidationError as exc:
        # A rejected file type or size is a media-type problem, not a malformed request
        # body; 415 lets the UI show "this format is not supported" precisely.
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return _augment(db, report)


@router.post("/manual", response_model=ProcessReportOut, status_code=201)
def create_manual_report(
    payload: ManualReportIn, db: DbSession, actor: User = Depends(require_auditor)
) -> ProcessReportOut:
    body = payload.model_dump(exclude_none=True)
    body["events"] = list(body.get("events", []))
    try:
        report = ingest_manual_report(db, payload=body, uploaded_by_id=actor.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return _augment(db, report)


@router.get("/{report_id}", response_model=ProcessReportDetail)
def get_report(report_id: int, db: DbSession, user: CurrentUser) -> ProcessReportDetail:
    report = db.get(ProcessReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Process report not found.")
    detail = ProcessReportDetail.model_validate(report)
    detail.events = [
        ReportEventOut.model_validate(e)
        for e in db.scalars(
            select(ReportEvent).where(ReportEvent.report_id == report_id).order_by(ReportEvent.seq)
        )
    ]
    detail.event_count = len(detail.events)
    detail.audit_count = db.scalar(select(func.count(Audit.id)).where(Audit.report_id == report_id)) or 0
    return detail
