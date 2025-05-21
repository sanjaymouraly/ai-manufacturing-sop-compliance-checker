"""Process execution report ingestion."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_setup import get_logger
from app.models.entities import ApiAuditEvent, ProcessReport, ReportEvent
from app.services.ingestion.extractors import ExtractedDocument, extract_document
from app.services.ingestion.storage import store_file, validate_upload
from app.services.parsing.report_parser import parse_report
from app.utils.injection import scan_text
from app.utils.text import sha256_bytes, sha256_text

logger = get_logger(__name__)

_SOP_CODE_RE = re.compile(r"\b(SOP[-_ ]?[A-Z]{2,6}[-_ ]?\d{2,5})\b", re.I)


class DuplicateReportError(ValueError):
    def __init__(self, existing: ProcessReport):
        super().__init__(f"This report was already uploaded as {existing.report_code}.")
        self.existing = existing


def _next_report_code(db: Session) -> str:
    count = db.scalar(select(func.count(ProcessReport.id))) or 0
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return f"PR-{stamp}-{count + 1:04d}"


def ingest_process_report(
    db: Session,
    *,
    data: bytes,
    filename: str,
    uploaded_by_id: int | None = None,
    overrides: dict[str, Any] | None = None,
    allow_duplicate: bool = False,
) -> ProcessReport:
    settings = get_settings()
    safe_name = validate_upload(filename, data)
    digest = sha256_bytes(data)

    existing = db.scalar(select(ProcessReport).where(ProcessReport.file_hash == digest))
    if existing is not None and not allow_duplicate:
        raise DuplicateReportError(existing)

    extracted = extract_document(data, safe_name, enable_ocr=settings.enable_ocr)
    return _persist_report(
        db,
        extracted=extracted,
        data=data,
        filename=safe_name,
        digest=digest,
        uploaded_by_id=uploaded_by_id,
        overrides=overrides or {},
    )


def ingest_manual_report(
    db: Session, *, payload: dict[str, Any], uploaded_by_id: int | None = None
) -> ProcessReport:
    """Manual entry path: the structured payload is stored as a JSON document."""
    body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    filename = f"{payload.get('report_code') or 'manual-entry'}.json"
    digest = sha256_bytes(body)
    extracted = extract_document(body, filename, enable_ocr=False)
    return _persist_report(
        db,
        extracted=extracted,
        data=body,
        filename=filename,
        digest=digest,
        uploaded_by_id=uploaded_by_id,
        overrides={},
        source="manual_entry",
    )


def _persist_report(
    db: Session,
    *,
    extracted: ExtractedDocument,
    data: bytes,
    filename: str,
    digest: str,
    uploaded_by_id: int | None,
    overrides: dict[str, Any],
    source: str = "upload",
) -> ProcessReport:
    parsed = parse_report(extracted)
    path, _ = store_file(data, filename, subdir="reports")
    injection_hits = scan_text(extracted.text)

    report_code = overrides.get("report_code") or parsed.report_code or _next_report_code(db)
    if db.scalar(select(ProcessReport).where(ProcessReport.report_code == report_code)):
        report_code = f"{report_code}-{sha256_text(digest)[:4]}"

    declared_sop = overrides.get("declared_sop_code") or parsed.declared_sop_code
    if not declared_sop:
        match = _SOP_CODE_RE.search(extracted.text)
        if match:
            declared_sop = re.sub(r"[_\s]", "-", match.group(1)).upper()
    if declared_sop:
        declared_sop = re.sub(r"[_\s]", "-", declared_sop).upper()

    report = ProcessReport(
        report_code=report_code,
        title=overrides.get("title") or parsed.title or report_code,
        batch_id=overrides.get("batch_id") or parsed.batch_id,
        line_id=overrides.get("line_id") or parsed.line_id,
        product_code=overrides.get("product_code") or parsed.product_code,
        site=overrides.get("site") or parsed.site,
        operator=overrides.get("operator") or parsed.operator,
        executed_at=_as_date(overrides.get("executed_at")) or parsed.executed_at,
        declared_sop_code=declared_sop,
        declared_revision_code=(
            str(overrides.get("declared_revision_code"))
            if overrides.get("declared_revision_code")
            else parsed.declared_revision_code
        ),
        file_name=filename,
        file_hash=digest,
        file_size=len(data),
        storage_path=str(path),
        source_format=extracted.source_format,
        raw_text=extracted.text,
        extraction_confidence=extracted.confidence,
        ocr_used=extracted.ocr_used,
        extraction_warnings=extracted.warnings + parsed.warnings,
        injection_flags=[hit.to_dict() for hit in injection_hits],
        uploaded_by_id=uploaded_by_id,
    )
    db.add(report)
    db.flush()

    for event in parsed.events:
        db.add(
            ReportEvent(
                report_id=report.id,
                seq=event.seq,
                step_number=event.step_number,
                text=event.text,
                status=event.status,
                operator=event.operator or report.operator,
                timestamp_text=event.timestamp_text,
                parameter_name=event.parameter_name,
                value_num=event.value_num,
                value_text=event.value_text,
                unit=event.unit,
                page=event.page,
                line_ref=event.line_ref,
                char_start=event.char_start,
                char_end=event.char_end,
                raw_fields=event.raw_fields,
            )
        )
    db.flush()

    db.add(
        ApiAuditEvent(
            event_type="report_ingested",
            actor_id=uploaded_by_id,
            entity_type="process_report",
            entity_id=report.id,
            message=f"Ingested process report {report.report_code} ({source})",
            payload={
                "events": len(parsed.events),
                "declared_sop_code": report.declared_sop_code,
                "declared_revision_code": report.declared_revision_code,
                "injection_flags": len(injection_hits),
                "sha256": digest,
            },
        )
    )
    logger.info("Ingested report %s with %d events", report.report_code, len(parsed.events))
    return report


def _as_date(value: Any):
    if not value:
        return None
    from dateutil import parser as date_parser

    try:
        return date_parser.parse(str(value)).date()
    except (ValueError, TypeError, OverflowError):
        return None
