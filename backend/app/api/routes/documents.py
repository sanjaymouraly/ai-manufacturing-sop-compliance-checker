"""SOP document library endpoints."""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select

from app.deps import (
    CurrentUser,
    DbSession,
    Pagination,
    record_event,
    require_auditor,
)
from app.models.entities import (
    DocumentSection,
    Requirement,
    SopConflict,
    SopDocument,
    SopRevision,
    User,
)
from app.models.enums import ConflictStatus, RevisionStatus
from app.schemas import (
    DiffOut,
    DocumentOut,
    DocumentUpdate,
    MessageOut,
    Page,
    RequirementOut,
    RevisionDetail,
    RevisionOut,
    RevisionStatusUpdate,
    SectionOut,
)
from app.services.audit import conflicts as conflict_engine
from app.services.documents import (
    DuplicateUploadError,
    ingest_sop_document,
    set_revision_status,
)
from app.services.ingestion.storage import UploadValidationError, verify_checksum
from app.services.versioning.diff import diff_revisions

router = APIRouter(prefix="/documents", tags=["documents"])


def _revision_out(db: DbSession, revision: SopRevision) -> RevisionOut:
    out = RevisionOut.model_validate(revision)
    out.requirement_count = (
        db.scalar(select(func.count(Requirement.id)).where(Requirement.revision_id == revision.id)) or 0
    )
    return out


def _documents_with_open_conflicts(db: DbSession, document_ids: list[int]) -> set[int]:
    """Document ids carrying at least one unresolved conflict (single grouped query)."""
    if not document_ids:
        return set()
    rows = db.scalars(
        select(SopConflict.document_id).where(
            SopConflict.document_id.in_(document_ids),
            SopConflict.status.in_([ConflictStatus.OPEN, ConflictStatus.ESCALATED]),
        )
    ).all()
    return {row for row in rows if row is not None}


def _document_out(db: DbSession, document: SopDocument, *, conflicted: set[int]) -> DocumentOut:
    out = DocumentOut.model_validate(document)
    revisions = [_revision_out(db, r) for r in document.revisions]
    out.revisions = revisions
    out.revision_count = len(revisions)
    if revisions:
        # "Latest" is ordering only - it carries no authority on its own. The version
        # resolver decides which revision actually governs a run.
        ordered = sorted(revisions, key=lambda r: (r.effective_date or date.min, r.revision_code))
        out.latest_revision_code = ordered[-1].revision_code
        approved = [r for r in ordered if r.status == RevisionStatus.APPROVED]
        # Exactly one approved revision is unambiguous. More than one means authority is
        # contested, so advertise none rather than guessing - the resolver will block.
        out.approved_revision_code = approved[0].revision_code if len(approved) == 1 else None
    out.has_open_conflicts = document.id in conflicted
    return out


@router.get("", response_model=Page[DocumentOut])
def list_documents(
    db: DbSession,
    user: CurrentUser,
    pagination: Pagination,
    search: str | None = None,
    process_area: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    include_archived: bool = False,
) -> Page[DocumentOut]:
    stmt = select(SopDocument)
    if not include_archived:
        stmt = stmt.where(SopDocument.is_archived.is_(False))
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                SopDocument.sop_code.ilike(pattern),
                SopDocument.title.ilike(pattern),
                SopDocument.process_area.ilike(pattern),
                SopDocument.owner.ilike(pattern),
            )
        )
    if process_area:
        stmt = stmt.where(SopDocument.process_area == process_area)
    if status_filter:
        stmt = stmt.where(
            SopDocument.id.in_(select(SopRevision.document_id).where(SopRevision.status == status_filter))
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(SopDocument.sop_code).offset(pagination.offset).limit(pagination.page_size)
    ).all()

    conflicted = _documents_with_open_conflicts(db, [d.id for d in rows])
    items = [_document_out(db, document, conflicted=conflicted) for document in rows]
    return Page[DocumentOut](
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=max(1, -(-total // pagination.page_size)),
    )


@router.post("/upload", response_model=RevisionOut, status_code=201)
async def upload_sop(
    db: DbSession,
    file: Annotated[UploadFile, File(description="SOP file (.pdf, .docx, .txt, .md, .csv, .json)")],
    actor: Annotated[User, Depends(require_auditor)],
    sop_code: Annotated[str | None, Form()] = None,
    revision_code: Annotated[str | None, Form()] = None,
    status_override: Annotated[str | None, Form()] = None,
    effective_date: Annotated[str | None, Form()] = None,
    expiry_date: Annotated[str | None, Form()] = None,
    approved_by: Annotated[str | None, Form()] = None,
    supersedes_revision_code: Annotated[str | None, Form()] = None,
    applicability_json: Annotated[str | None, Form()] = None,
    allow_duplicate: Annotated[bool, Form()] = False,
) -> RevisionOut:
    data = await file.read()
    overrides: dict[str, Any] = {
        "sop_code": sop_code or None,
        "revision_code": revision_code or None,
        "status": status_override or None,
        "effective_date": effective_date or None,
        "expiry_date": expiry_date or None,
        "approved_by": approved_by or None,
        "supersedes_revision_code": supersedes_revision_code or None,
        "mime_type": file.content_type,
    }
    if applicability_json:
        try:
            overrides["applicability"] = json.loads(applicability_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"applicability_json is invalid: {exc.msg}",
            ) from exc
    overrides = {k: v for k, v in overrides.items() if v is not None}

    try:
        revision = ingest_sop_document(
            db,
            data=data,
            filename=file.filename or "upload",
            uploaded_by_id=actor.id,
            overrides=overrides,
            allow_duplicate=allow_duplicate,
        )
    except DuplicateUploadError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except UploadValidationError as exc:
        # A rejected file type or size is a media-type problem, not a malformed request
        # body; 415 lets the UI show "this format is not supported" precisely.
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return _revision_out(db, revision)


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, db: DbSession, user: CurrentUser) -> DocumentOut:
    document = db.get(SopDocument, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SOP document not found.")
    return _document_out(db, document, conflicted=_documents_with_open_conflicts(db, [document.id]))


@router.patch("/{document_id}", response_model=DocumentOut)
def update_document(
    document_id: int, payload: DocumentUpdate, db: DbSession, actor: User = Depends(require_auditor)
) -> DocumentOut:
    document = db.get(SopDocument, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SOP document not found.")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(document, field, value)
    record_event(
        db,
        event_type="document_updated",
        actor=actor,
        entity_type="sop_document",
        entity_id=document.id,
        message=f"Updated {document.sop_code}",
        payload=payload.model_dump(exclude_none=True),
    )
    out = DocumentOut.model_validate(document)
    out.revisions = [_revision_out(db, r) for r in document.revisions]
    return out


@router.get("/revisions/{revision_id}", response_model=RevisionDetail)
def get_revision(revision_id: int, db: DbSession, user: CurrentUser) -> RevisionDetail:
    revision = db.get(SopRevision, revision_id)
    if revision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Revision not found.")
    detail = RevisionDetail.model_validate(revision)
    detail.sections = [
        SectionOut.model_validate(s)
        for s in db.scalars(
            select(DocumentSection)
            .where(DocumentSection.revision_id == revision_id)
            .order_by(DocumentSection.order_index)
        )
    ]
    detail.requirements = [
        RequirementOut.model_validate(r)
        for r in db.scalars(
            select(Requirement).where(Requirement.revision_id == revision_id).order_by(Requirement.seq)
        )
    ]
    detail.requirement_count = len(detail.requirements)
    return detail


@router.get("/revisions/{revision_id}/file")
def download_revision_file(revision_id: int, db: DbSession, user: CurrentUser) -> FileResponse:
    revision = db.get(SopRevision, revision_id)
    if revision is None or not revision.storage_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stored file not found.")
    if not verify_checksum(revision.storage_path, revision.file_hash):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Stored file failed checksum verification; the file may have been modified on disk.",
        )
    return FileResponse(revision.storage_path, filename=revision.file_name)


@router.patch("/revisions/{revision_id}/status", response_model=RevisionOut)
def update_revision_status(
    revision_id: int,
    payload: RevisionStatusUpdate,
    db: DbSession,
    actor: User = Depends(require_auditor),
) -> RevisionOut:
    revision = db.get(SopRevision, revision_id)
    if revision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Revision not found.")
    try:
        set_revision_status(db, revision, payload.status, actor_id=actor.id, reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    db.flush()
    conflict_engine.scan_document(db, revision.document)
    return _revision_out(db, revision)


@router.get("/revisions/{left_id}/diff/{right_id}", response_model=DiffOut)
def diff(left_id: int, right_id: int, db: DbSession, user: CurrentUser) -> DiffOut:
    left = db.get(SopRevision, left_id)
    right = db.get(SopRevision, right_id)
    if left is None or right is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "One or both revisions were not found.")
    left_reqs = list(
        db.scalars(select(Requirement).where(Requirement.revision_id == left_id).order_by(Requirement.seq))
    )
    right_reqs = list(
        db.scalars(select(Requirement).where(Requirement.revision_id == right_id).order_by(Requirement.seq))
    )
    result = diff_revisions(left, right, left_reqs, right_reqs)
    return DiffOut(**result.to_dict())


@router.post("/scan-conflicts", response_model=MessageOut)
def scan_conflicts(db: DbSession, actor: User = Depends(require_auditor)) -> MessageOut:
    found = conflict_engine.scan_library(db)
    record_event(
        db,
        event_type="conflict_scan",
        actor=actor,
        message=f"Library conflict scan completed: {len(found)} conflict(s) on record",
    )
    return MessageOut(
        message=f"Conflict scan complete. {len(found)} conflict record(s) now exist for the library.",
        detail={"conflicts": len(found)},
    )
