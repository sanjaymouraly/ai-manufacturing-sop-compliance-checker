"""SOP document ingestion and library operations."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_setup import get_logger
from app.models.entities import (
    ApiAuditEvent,
    DocumentSection,
    Requirement,
    SopDocument,
    SopRevision,
)
from app.models.enums import RevisionStatus
from app.services.audit import conflicts as conflict_engine
from app.services.ingestion.extractors import extract_document
from app.services.ingestion.storage import store_file, validate_upload
from app.services.parsing.metadata import extract_sop_metadata
from app.services.parsing.requirements import extract_requirements
from app.services.parsing.structure import parse_structure
from app.utils.injection import scan_text
from app.utils.text import sha256_bytes

logger = get_logger(__name__)


class DuplicateUploadError(ValueError):
    def __init__(self, existing: SopRevision):
        super().__init__(
            f"This file was already uploaded as {existing.file_name} "
            f"({existing.document.sop_code if existing.document else ''} {existing.revision_code})."
        )
        self.existing = existing


def find_by_hash(db: Session, digest: str) -> SopRevision | None:
    return db.scalar(select(SopRevision).where(SopRevision.file_hash == digest))


def ingest_sop_document(
    db: Session,
    *,
    data: bytes,
    filename: str,
    uploaded_by_id: int | None = None,
    overrides: dict[str, Any] | None = None,
    allow_duplicate: bool = False,
    run_conflict_scan: bool = True,
) -> SopRevision:
    """Full ingestion: validate → extract → parse → persist → conflict scan."""
    settings = get_settings()
    safe_name = validate_upload(filename, data)
    digest = sha256_bytes(data)

    existing = find_by_hash(db, digest)
    if existing is not None and not allow_duplicate:
        raise DuplicateUploadError(existing)

    extracted = extract_document(data, safe_name, enable_ocr=settings.enable_ocr)
    metadata = extract_sop_metadata(extracted.text, fallback_filename=safe_name)
    overrides = overrides or {}

    sop_code = overrides.get("sop_code") or metadata.sop_code
    if not sop_code:
        raise ValueError(
            "The SOP code could not be determined from the document. Provide it explicitly "
            "(expected a code such as 'SOP-ASSY-101')."
        )
    revision_code = overrides.get("revision_code") or metadata.revision_code
    if not revision_code:
        raise ValueError(
            "The revision identifier could not be determined. Provide it explicitly (e.g. '3' or 'B')."
        )

    document = db.scalar(select(SopDocument).where(SopDocument.sop_code == sop_code))
    if document is None:
        document = SopDocument(
            sop_code=sop_code,
            title=overrides.get("title") or metadata.title or sop_code,
            process_area=overrides.get("process_area") or metadata.process_area,
            owner=overrides.get("owner") or metadata.owner,
        )
        db.add(document)
        db.flush()

    duplicate_revision = db.scalar(
        select(SopRevision).where(
            SopRevision.document_id == document.id, SopRevision.revision_code == revision_code
        )
    )
    if duplicate_revision is not None:
        raise ValueError(
            f"Revision '{revision_code}' already exists for {sop_code}. Archive it first or use a new "
            "revision identifier."
        )

    path, _ = store_file(data, safe_name, subdir="sops")
    injection_hits = scan_text(extracted.text)

    status = overrides.get("status") or metadata.status
    if status not in set(RevisionStatus):
        status = RevisionStatus.UNKNOWN

    revision = SopRevision(
        document_id=document.id,
        revision_code=revision_code,
        title=overrides.get("title") or metadata.title or document.title,
        status=status,
        effective_date=_as_date(overrides.get("effective_date")) or metadata.effective_date,
        expiry_date=_as_date(overrides.get("expiry_date")) or metadata.expiry_date,
        approved_by=overrides.get("approved_by") or metadata.approved_by,
        approval_date=_as_date(overrides.get("approval_date")) or metadata.approval_date,
        supersedes_revision_code=overrides.get("supersedes_revision_code")
        or metadata.supersedes_revision_code,
        superseded_by_revision_code=overrides.get("superseded_by_revision_code")
        or metadata.superseded_by_revision_code,
        applicability=overrides.get("applicability") or metadata.applicability,
        file_name=safe_name,
        file_hash=digest,
        file_size=len(data),
        mime_type=overrides.get("mime_type"),
        storage_path=str(path),
        source_format=extracted.source_format,
        page_count=extracted.page_count,
        extraction_confidence=extracted.confidence,
        ocr_used=extracted.ocr_used,
        extraction_warnings=extracted.warnings + metadata.warnings,
        injection_flags=[hit.to_dict() for hit in injection_hits],
        raw_text=extracted.text,
        uploaded_by_id=uploaded_by_id,
    )
    db.add(revision)
    db.flush()

    sections = parse_structure(extracted)
    section_rows: list[DocumentSection] = []
    for section in sections:
        row = DocumentSection(
            revision_id=revision.id,
            section_path=section.section_path,
            heading=section.heading,
            level=section.level,
            kind=section.kind,
            page=section.page,
            order_index=section.order_index,
            char_start=section.char_start,
            char_end=section.char_end,
            text=section.text,
        )
        section_rows.append(row)
        db.add(row)
    db.flush()

    extracted_requirements = extract_requirements(sections, sop_code=sop_code, revision_code=revision_code)
    for item in extracted_requirements:
        section_row = (
            section_rows[item.section_id_hint]
            if item.section_id_hint is not None and item.section_id_hint < len(section_rows)
            else None
        )
        db.add(
            Requirement(
                revision_id=revision.id,
                section_id=section_row.id if section_row else None,
                req_key=item.req_key,
                seq=item.seq,
                step_number=item.step_number,
                text=item.text,
                normalized_text=item.normalized_text,
                obligation=item.obligation,
                action_verb=item.action_verb,
                condition_text=item.condition_text,
                requires_record=item.requires_record,
                record_field=item.record_field,
                parameter_name=item.parameter_name,
                comparator=item.comparator,
                value_min=item.value_min,
                value_max=item.value_max,
                nominal=item.nominal,
                tolerance=item.tolerance,
                unit=item.unit,
                section_path=item.section_path,
                page=item.page,
                char_start=item.char_start,
                char_end=item.char_end,
                extraction_method=item.extraction_method,
                confidence=item.confidence,
                ambiguity_notes=item.ambiguity_notes,
            )
        )
    db.flush()

    db.add(
        ApiAuditEvent(
            event_type="sop_uploaded",
            actor_id=uploaded_by_id,
            entity_type="sop_revision",
            entity_id=revision.id,
            message=f"Uploaded {sop_code} {revision_code} ({safe_name})",
            payload={
                "sections": len(section_rows),
                "requirements": len(extracted_requirements),
                "status": status,
                "extraction_confidence": extracted.confidence,
                "injection_flags": len(injection_hits),
                "sha256": digest,
            },
        )
    )
    logger.info(
        "Ingested %s %s: %d sections, %d requirements, status=%s",
        sop_code,
        revision_code,
        len(section_rows),
        len(extracted_requirements),
        status,
    )

    if run_conflict_scan:
        db.flush()
        db.refresh(document)
        conflict_engine.scan_document(db, document)
    return revision


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    from dateutil import parser as date_parser

    try:
        return date_parser.parse(str(value)).date()
    except (ValueError, TypeError, OverflowError):
        return None


def document_stats(db: Session) -> dict[str, Any]:
    total_docs = db.scalar(select(func.count(SopDocument.id))) or 0
    total_revisions = db.scalar(select(func.count(SopRevision.id))) or 0
    by_status = dict(
        db.execute(select(SopRevision.status, func.count(SopRevision.id)).group_by(SopRevision.status)).all()
    )
    return {
        "documents": total_docs,
        "revisions": total_revisions,
        "revisions_by_status": by_status,
    }


def set_revision_status(
    db: Session, revision: SopRevision, status: str, *, actor_id: int | None, reason: str
) -> SopRevision:
    if status not in set(RevisionStatus):
        raise ValueError(f"Unknown status '{status}'.")
    previous = revision.status
    revision.status = status
    db.add(
        ApiAuditEvent(
            event_type="revision_status_changed",
            actor_id=actor_id,
            entity_type="sop_revision",
            entity_id=revision.id,
            message=f"Status {previous} → {status}",
            payload={"reason": reason, "from": previous, "to": status},
        )
    )
    db.flush()
    return revision
