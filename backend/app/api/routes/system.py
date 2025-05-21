"""Health, analytics, exports and the system event log."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select

from app.config import get_settings
from app.deps import CurrentUser, DbSession, Pagination, record_event, require_auditor
from app.logging_setup import get_logger
from app.models.entities import ApiAuditEvent, Audit, ExportArtifact, SopConflict, SopDocument, User
from app.models.enums import ConflictStatus
from app.schemas import HealthOut
from app.services import analytics as analytics_service
from app.services.llm.service import get_active_config
from app.services.reporting.generator import MEDIA_TYPES, export_audit

logger = get_logger(__name__)

router = APIRouter(tags=["system"])
START_TIME = time.time()


@router.get("/health", response_model=HealthOut)
def health(db: DbSession) -> HealthOut:
    settings = get_settings()
    # A health probe must always answer. Every database touch lives inside the
    # try block so an unreachable database reports "degraded" instead of raising
    # a 500 - which would make the endpoint useless to the thing monitoring it.
    database = "ok"
    documents = audits = open_conflicts = 0
    llm_provider = settings.llm_provider
    llm_configured = bool(settings.llm_api_key)
    try:
        documents = db.scalar(select(func.count(SopDocument.id))) or 0
        audits = db.scalar(select(func.count(Audit.id))) or 0
        open_conflicts = (
            db.scalar(
                select(func.count(SopConflict.id)).where(
                    SopConflict.status.in_([ConflictStatus.OPEN, ConflictStatus.ESCALATED])
                )
            )
            or 0
        )
        config = get_active_config(db)
        if config is not None:
            llm_provider = config.provider_type
            llm_configured = llm_configured or bool(config.api_key_encrypted)
    except Exception as exc:
        database = f"error: {exc.__class__.__name__}"
        logger.warning("Health check could not reach the database: %s", exc)
    return HealthOut(
        status="ok" if database == "ok" else "degraded",
        version=settings.app_version,
        environment=settings.environment,
        database=database,
        analysis_mode=settings.analysis_mode,
        llm_provider=llm_provider,
        llm_configured=llm_configured,
        documents=documents,
        audits=audits,
        open_conflicts=open_conflicts,
        uptime_seconds=round(time.time() - START_TIME, 1),
    )


analytics_router = APIRouter(prefix="/analytics", tags=["analytics"])


@analytics_router.get("/dashboard")
def dashboard(db: DbSession, user: CurrentUser, days: int = Query(default=90, ge=1, le=730)) -> dict:
    return analytics_service.dashboard(db, days=days)


@analytics_router.get("/trends")
def trends(db: DbSession, user: CurrentUser, days: int = Query(default=180, ge=1, le=730)) -> list[dict]:
    return analytics_service.finding_trends(db, days=days)


@analytics_router.get("/reviewers")
def reviewers(db: DbSession, user: CurrentUser) -> list[dict]:
    return analytics_service.reviewer_activity(db)


exports_router = APIRouter(prefix="/exports", tags=["exports"])


@exports_router.get("")
def list_exports(
    db: DbSession, user: CurrentUser, pagination: Pagination, audit_id: int | None = None
) -> dict:
    stmt = select(ExportArtifact)
    if audit_id:
        stmt = stmt.where(ExportArtifact.audit_id == audit_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(ExportArtifact.created_at.desc()).offset(pagination.offset).limit(pagination.page_size)
    ).all()
    # Export artefacts are listed by humans, so resolve the audit code and the creator
    # rather than making the client join on raw ids.
    return {
        "items": [
            {
                "id": r.id,
                "audit_id": r.audit_id,
                "audit_code": r.audit.audit_code if r.audit else None,
                "format": r.fmt,
                "version": r.version,
                "file_name": r.file_name,
                "file_size": r.file_size,
                "sha256": r.sha256,
                "created_at": r.created_at.isoformat(),
                "created_by": r.created_by.username if r.created_by else None,
                "download_url": f"/api/exports/{r.id}/download",
            }
            for r in rows
        ],
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "pages": max(1, (total + pagination.page_size - 1) // pagination.page_size),
    }


@exports_router.post("/audits/{audit_id}/{fmt}")
def create_export(audit_id: int, fmt: str, db: DbSession, actor: User = Depends(require_auditor)) -> dict:
    audit = db.get(Audit, audit_id)
    if audit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audit not found.")
    try:
        artifact = export_audit(db, audit, fmt, created_by_id=actor.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    record_event(
        db,
        event_type="export_created",
        actor=actor,
        entity_type="audit",
        entity_id=audit.id,
        message=f"Exported {audit.audit_code} as {fmt} (v{artifact.version})",
    )
    return {
        "id": artifact.id,
        "audit_id": audit.id,
        "format": artifact.fmt,
        "version": artifact.version,
        "file_name": artifact.file_name,
        "file_size": artifact.file_size,
        "sha256": artifact.sha256,
        "download_url": f"/api/exports/{artifact.id}/download",
    }


@exports_router.get("/audits/{audit_id}/{fmt}/inline")
def inline_export(audit_id: int, fmt: str, db: DbSession, user: CurrentUser) -> Response:
    """Render an export on the fly without persisting an artefact (preview)."""
    from app.services.reporting.generator import RENDERERS, build_audit_payload

    audit = db.get(Audit, audit_id)
    if audit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audit not found.")
    if fmt not in RENDERERS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unsupported format '{fmt}'.")
    data = RENDERERS[fmt](build_audit_payload(db, audit))
    return Response(
        content=data,
        media_type=MEDIA_TYPES[fmt],
        headers={"Content-Disposition": f'inline; filename="{audit.audit_code}.{fmt}"'},
    )


@exports_router.get("/{export_id}/download")
def download_export(export_id: int, db: DbSession, user: CurrentUser) -> FileResponse:
    artifact = db.get(ExportArtifact, export_id)
    if artifact is None or not Path(artifact.storage_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export artefact not found.")
    return FileResponse(
        artifact.storage_path,
        filename=artifact.file_name,
        media_type=MEDIA_TYPES.get(artifact.fmt, "application/octet-stream"),
    )


events_router = APIRouter(prefix="/events", tags=["system"])


@events_router.get("")
def list_events(
    db: DbSession,
    user: CurrentUser,
    pagination: Pagination,
    event_type: str | None = None,
) -> dict:
    stmt = select(ApiAuditEvent)
    if event_type:
        stmt = stmt.where(ApiAuditEvent.event_type == event_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(ApiAuditEvent.created_at.desc()).offset(pagination.offset).limit(pagination.page_size)
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "event_type": r.event_type,
                "actor": r.actor_name,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "message": r.message,
                "payload": r.payload,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
    }


settings_router = APIRouter(prefix="/settings", tags=["system"])


@settings_router.get("")
def read_settings(user: CurrentUser) -> dict:
    """Non-sensitive runtime configuration for the UI."""
    s = get_settings()
    return {
        "app_name": s.app_name,
        "version": s.app_version,
        "environment": s.environment,
        "analysis_mode": s.analysis_mode,
        "evidence_match_threshold": s.evidence_match_threshold,
        "strict_citation_verification": s.strict_citation_verification,
        "enable_ocr": s.enable_ocr,
        "max_upload_mb": s.max_upload_mb,
        "allowed_upload_extensions": sorted(s.upload_extensions),
        "retention_days": s.retention_days,
        "rate_limit_per_minute": s.rate_limit_per_minute,
    }
