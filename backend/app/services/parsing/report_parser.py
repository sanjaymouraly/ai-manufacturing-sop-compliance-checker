"""Process execution report parsing into normalised events.

Three input shapes are supported, all producing the same ``ParsedEvent`` model
with character offsets into the stored raw text:

* **JSON** – the canonical structured shape (see ``docs/api.md``).
* **CSV** – one row per executed step.
* **Free text / PDF / DOCX** – line-oriented heuristics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from dateutil import parser as date_parser

from app.models.enums import EventStatus
from app.services.ingestion.extractors import ExtractedDocument
from app.utils.text import find_span, normalize_whitespace
from app.utils.units import canonical_unit, is_known_unit

_STATUS_WORDS = {
    "completed": EventStatus.COMPLETED,
    "complete": EventStatus.COMPLETED,
    "done": EventStatus.COMPLETED,
    "ok": EventStatus.COMPLETED,
    "pass": EventStatus.COMPLETED,
    "passed": EventStatus.COMPLETED,
    "yes": EventStatus.COMPLETED,
    "performed": EventStatus.COMPLETED,
    "executed": EventStatus.COMPLETED,
    "skipped": EventStatus.SKIPPED,
    "skip": EventStatus.SKIPPED,
    "omitted": EventStatus.SKIPPED,
    "not performed": EventStatus.SKIPPED,
    "not done": EventStatus.SKIPPED,
    "n/a": EventStatus.NOT_APPLICABLE,
    "na": EventStatus.NOT_APPLICABLE,
    "not applicable": EventStatus.NOT_APPLICABLE,
    "failed": EventStatus.FAILED,
    "fail": EventStatus.FAILED,
    "reject": EventStatus.FAILED,
    "nok": EventStatus.FAILED,
    "error": EventStatus.FAILED,
}

_STEP_LINE = re.compile(r"^(?:step\s*)?(?P<step>\d+(?:\.\d+)*)[.)\]:-]?\s+(?P<body>.+)$", re.I)
_KV = re.compile(r"^\s*\"?(?P<key>[A-Za-z_][A-Za-z0-9_ ]{1,40})\"?\s*[:=]\s*(?P<value>.+?)\s*,?$")
_VALUE_UNIT = re.compile(r"(?P<num>-?\d+(?:[.,]\d+)?)\s*(?P<unit>°\s?[CFK]|%|[A-Za-zµ][A-Za-z·.\-]{0,9})?")
_TIME_RE = re.compile(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\b")
_OPERATOR_RE = re.compile(r"\b(?:by|operator|tech(?:nician)?)\s*[:\-]?\s*([A-Z][A-Za-z.'\- ]{1,30})", re.I)


@dataclass
class ParsedEvent:
    seq: int
    text: str
    status: str = EventStatus.UNKNOWN
    step_number: str | None = None
    operator: str | None = None
    timestamp_text: str | None = None
    parameter_name: str | None = None
    value_num: float | None = None
    value_text: str | None = None
    unit: str | None = None
    page: int = 1
    line_ref: str | None = None
    char_start: int = 0
    char_end: int = 0
    raw_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedReport:
    events: list[ParsedEvent] = field(default_factory=list)
    report_code: str | None = None
    title: str | None = None
    batch_id: str | None = None
    line_id: str | None = None
    product_code: str | None = None
    site: str | None = None
    operator: str | None = None
    executed_at: date | None = None
    declared_sop_code: str | None = None
    declared_revision_code: str | None = None
    operator_notes: str | None = None
    warnings: list[str] = field(default_factory=list)


def _normalise_status(value: Any) -> str:
    if value is None:
        return EventStatus.UNKNOWN
    text = normalize_whitespace(str(value)).lower().strip(" .;")
    return _STATUS_WORDS.get(text, EventStatus.UNKNOWN)


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date_parser.parse(str(value), fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def _clean_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    u = unit.strip().strip(".,;:")
    if not u or canonical_unit(u) is None or not is_known_unit(u):
        return None
    return u


def _split_value_unit(raw: Any) -> tuple[float | None, str | None, str | None]:
    """Return ``(numeric, unit, text)`` for a measurement cell."""
    if raw is None or raw == "":
        return None, None, None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw), None, str(raw)
    text = normalize_whitespace(str(raw))
    m = _VALUE_UNIT.search(text)
    if not m:
        return None, None, text
    try:
        number = float(m.group("num").replace(",", "."))
    except ValueError:
        return None, None, text
    return number, _clean_unit(m.group("unit")), text


def parse_json_report(payload: dict[str, Any], raw_text: str) -> ParsedReport:
    report = ParsedReport(
        report_code=payload.get("report_code") or payload.get("id"),
        title=payload.get("title"),
        batch_id=payload.get("batch_id") or payload.get("batch"),
        line_id=payload.get("line_id") or payload.get("line"),
        product_code=payload.get("product_code") or payload.get("product"),
        site=payload.get("site") or payload.get("plant"),
        operator=payload.get("operator"),
        executed_at=_parse_date(payload.get("executed_at") or payload.get("date")),
        declared_sop_code=payload.get("sop_code") or payload.get("declared_sop_code"),
        declared_revision_code=(
            str(payload["sop_revision"])
            if payload.get("sop_revision") is not None
            else (
                str(payload["declared_revision_code"])
                if payload.get("declared_revision_code") is not None
                else None
            )
        ),
        operator_notes=payload.get("operator_notes") or payload.get("notes"),
    )
    raw_events = payload.get("events") or payload.get("steps") or []
    if not isinstance(raw_events, list):
        report.warnings.append("'events' is not a list; no events parsed.")
        raw_events = []

    cursor = 0
    for index, item in enumerate(raw_events, start=1):
        if not isinstance(item, dict):
            report.warnings.append(f"Event #{index} is not an object and was ignored.")
            continue
        description = normalize_whitespace(
            str(item.get("description") or item.get("text") or item.get("action") or "")
        )
        value_num, value_unit, value_text = _split_value_unit(item.get("value"))
        unit = _clean_unit(item.get("unit")) or value_unit
        if item.get("unit") and not _clean_unit(item.get("unit")):
            unit = normalize_whitespace(str(item.get("unit")))  # keep unknown unit for mismatch reporting
        span = find_span(raw_text, description, cursor) if description else None
        if span:
            cursor = span[1]
        report.events.append(
            ParsedEvent(
                seq=index,
                text=description,
                status=_normalise_status(item.get("status")),
                step_number=str(item["step"]) if item.get("step") is not None else item.get("step_number"),
                operator=item.get("operator"),
                timestamp_text=str(item.get("timestamp")) if item.get("timestamp") else None,
                parameter_name=item.get("parameter") or item.get("parameter_name"),
                value_num=value_num,
                value_text=value_text,
                unit=unit,
                char_start=span[0] if span else 0,
                char_end=span[1] if span else 0,
                line_ref=f"events[{index - 1}]",
                raw_fields={k: v for k, v in item.items() if k not in {"description", "text"}},
            )
        )
    if report.operator_notes:
        span = find_span(raw_text, normalize_whitespace(report.operator_notes))
        report.events.append(
            ParsedEvent(
                seq=len(report.events) + 1,
                text=normalize_whitespace(report.operator_notes),
                status=EventStatus.UNKNOWN,
                line_ref="operator_notes",
                char_start=span[0] if span else 0,
                char_end=span[1] if span else 0,
                raw_fields={"kind": "operator_notes"},
            )
        )
    return report


def parse_csv_report(structured: dict[str, Any], raw_text: str) -> ParsedReport:
    report = ParsedReport()
    rows: list[dict[str, Any]] = structured.get("rows", [])  # type: ignore[assignment]
    lines = raw_text.split("\n")
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line) + 1

    def _get(row: dict[str, Any], *names: str) -> Any:
        for name in names:
            for key, value in row.items():
                if key and key.strip().lower().replace(" ", "_") == name:
                    return value
        return None

    for index, row in enumerate(rows, start=1):
        description = normalize_whitespace(
            str(_get(row, "description", "text", "action", "step_description") or "")
        )
        value_num, value_unit, value_text = _split_value_unit(_get(row, "value", "measurement", "result"))
        unit_raw = _get(row, "unit", "uom")
        unit = _clean_unit(unit_raw) or value_unit
        if unit_raw and not _clean_unit(unit_raw):
            unit = normalize_whitespace(str(unit_raw))
        line_index = min(index, len(lines) - 1)
        start = offsets[line_index] if line_index < len(offsets) else 0
        end = start + len(lines[line_index]) if line_index < len(lines) else 0
        report.events.append(
            ParsedEvent(
                seq=index,
                text=description,
                status=_normalise_status(_get(row, "status", "result", "outcome")),
                step_number=(
                    str(_get(row, "step", "step_number")) if _get(row, "step", "step_number") else None
                ),
                operator=_get(row, "operator", "technician", "performed_by"),
                timestamp_text=(
                    str(_get(row, "timestamp", "time", "datetime"))
                    if _get(row, "timestamp", "time", "datetime")
                    else None
                ),
                parameter_name=_get(row, "parameter", "parameter_name", "characteristic"),
                value_num=value_num,
                value_text=value_text,
                unit=unit,
                char_start=start,
                char_end=end,
                line_ref=f"row {index}",
                raw_fields=dict(row),
            )
        )
        if report.batch_id is None:
            report.batch_id = _get(row, "batch", "batch_id")
        if report.declared_sop_code is None:
            report.declared_sop_code = _get(row, "sop", "sop_code")
        if report.declared_revision_code is None and _get(row, "sop_revision", "revision"):
            report.declared_revision_code = str(_get(row, "sop_revision", "revision"))
        if report.operator is None:
            report.operator = _get(row, "operator", "technician")
        if report.executed_at is None:
            report.executed_at = _parse_date(_get(row, "date", "executed_at", "timestamp"))
    return report


_HEADER_FIELDS = {
    "report": "report_code",
    "report_id": "report_code",
    "report_code": "report_code",
    "title": "title",
    "batch": "batch_id",
    "batch_id": "batch_id",
    "lot": "batch_id",
    "line": "line_id",
    "line_id": "line_id",
    "product": "product_code",
    "product_code": "product_code",
    "site": "site",
    "plant": "site",
    "operator": "operator",
    "technician": "operator",
    "date": "executed_at",
    "executed": "executed_at",
    "executed_at": "executed_at",
    "execution_date": "executed_at",
    "sop": "declared_sop_code",
    "sop_code": "declared_sop_code",
    "procedure": "declared_sop_code",
    "sop_revision": "declared_revision_code",
    "revision": "declared_revision_code",
    "rev": "declared_revision_code",
    "sop_rev": "declared_revision_code",
    "notes": "operator_notes",
    "operator_notes": "operator_notes",
    "comments": "operator_notes",
}


def parse_text_report(doc: ExtractedDocument) -> ParsedReport:
    """Line-oriented parsing for TXT/MD/PDF/DOCX reports."""
    report = ParsedReport()
    text = doc.text
    cursor = 0
    seq = 0
    notes_buffer: list[str] = []
    in_notes = False

    for raw_line in text.split("\n"):
        start = cursor
        end = cursor + len(raw_line)
        cursor = end + 1
        line = raw_line.strip()
        if not line or line.startswith("[page "):
            continue

        if re.match(r"^#{1,6}\s*(operator\s+notes|notes|comments)\b", line, re.I) or re.match(
            r"^(operator\s+notes|notes|comments)\s*:?\s*$", line, re.I
        ):
            in_notes = True
            continue
        if line.startswith("#"):
            in_notes = False
            if report.title is None:
                report.title = normalize_whitespace(line.lstrip("# ").strip())
            continue

        if m := _STEP_LINE.match(line):
            in_notes = False
            seq += 1
            body = normalize_whitespace(m.group("body"))
            status = EventStatus.UNKNOWN
            for word, mapped in _STATUS_WORDS.items():
                if re.search(rf"\b{re.escape(word)}\b", body, re.I):
                    status = mapped
                    break
            value_num, unit, value_text = None, None, None
            if mv := re.search(
                r"(?:=|:|recorded|measured|reading|value|actual)\s*(?P<num>-?\d+(?:[.,]\d+)?)\s*(?P<unit>°\s?[CFK]|%|[A-Za-zµ][A-Za-z·.\-]{0,9})?",
                body,
                re.I,
            ):
                value_num, unit, value_text = _split_value_unit(
                    f"{mv.group('num')} {mv.group('unit') or ''}".strip()
                )
            operator = None
            if mo := _OPERATOR_RE.search(body):
                operator = normalize_whitespace(mo.group(1))
            timestamp = None
            if mt := _TIME_RE.search(body):
                timestamp = mt.group(1)
            report.events.append(
                ParsedEvent(
                    seq=seq,
                    text=body,
                    status=status,
                    step_number=m.group("step"),
                    operator=operator,
                    timestamp_text=timestamp,
                    value_num=value_num,
                    unit=unit,
                    value_text=value_text,
                    page=doc.page_for_offset(start),
                    char_start=start,
                    char_end=end,
                    line_ref=f"line @{start}",
                )
            )
            continue

        if m := _KV.match(line):
            key = m.group("key").strip().lower().replace(" ", "_")
            value = normalize_whitespace(m.group("value")).strip('",')
            mapped_field = _HEADER_FIELDS.get(key)
            if mapped_field and getattr(report, mapped_field, None) in (None, ""):
                if mapped_field == "executed_at":
                    report.executed_at = _parse_date(value)
                else:
                    setattr(report, mapped_field, value)
                in_notes = mapped_field == "operator_notes"
                continue

        if in_notes:
            notes_buffer.append(line)
            seq += 1
            report.events.append(
                ParsedEvent(
                    seq=seq,
                    text=normalize_whitespace(line),
                    status=EventStatus.UNKNOWN,
                    page=doc.page_for_offset(start),
                    char_start=start,
                    char_end=end,
                    line_ref="operator_notes",
                    raw_fields={"kind": "operator_notes"},
                )
            )

    if notes_buffer:
        report.operator_notes = " ".join(notes_buffer)
    if not report.events:
        report.warnings.append("No executed steps could be parsed from this report.")
    return report


def parse_report(doc: ExtractedDocument) -> ParsedReport:
    """Dispatch to the right parser based on the extracted format."""
    if doc.source_format == "json" and isinstance(doc.structured, dict):
        report = parse_json_report(doc.structured, doc.text)
    elif doc.source_format == "csv" and isinstance(doc.structured, dict):
        report = parse_csv_report(doc.structured, doc.text)
    else:
        report = parse_text_report(doc)

    for event in report.events:
        event.page = doc.page_for_offset(event.char_start) if doc.pages else 1
    report.warnings.extend(doc.warnings)
    return report
