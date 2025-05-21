"""Extraction of SOP lifecycle metadata from document text.

Metadata drives *version authority*, so unknown values are reported as unknown
rather than guessed. Anything the parser cannot determine is left ``None`` and
surfaces in the UI as "unknown", which in turn blocks definitive conclusions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from dateutil import parser as date_parser

from app.models.enums import RevisionStatus
from app.utils.text import normalize_whitespace

_FIELD_RE = r"[*_#\s]*{label}\s*[:\-–]\s*(?P<value>[^\n|]+)"

_PATTERNS = {
    "sop_code": re.compile(r"\b(?P<code>SOP[-_ ]?[A-Z]{2,6}[-_ ]?\d{2,5})\b", re.I),
    "revision": re.compile(_FIELD_RE.format(label=r"(?:revision|rev\.?|version)"), re.I),
    "status": re.compile(_FIELD_RE.format(label=r"(?:status|document\s+status|state)"), re.I),
    "effective": re.compile(
        _FIELD_RE.format(label=r"(?:effective\s*date|effective\s*from|valid\s*from)"), re.I
    ),
    "expiry": re.compile(
        _FIELD_RE.format(label=r"(?:expiry\s*date|expires?|valid\s*until|review\s*due|superseded\s*on)"), re.I
    ),
    "approved_by": re.compile(
        _FIELD_RE.format(label=r"(?:approved\s*by|authori[sz]ed\s*by|released\s*by)"), re.I
    ),
    "approval_date": re.compile(_FIELD_RE.format(label=r"(?:approval\s*date|date\s*approved)"), re.I),
    "supersedes": re.compile(_FIELD_RE.format(label=r"(?:supersedes|replaces)"), re.I),
    "superseded_by": re.compile(_FIELD_RE.format(label=r"(?:superseded\s*by|replaced\s*by)"), re.I),
    "applicability": re.compile(
        r"[*_#\s]*(?:applicability|applicable\s*to|scope\s*of\s*application)\s*[:\-\u2013]\s*(?P<value>[^\n]+)",
        re.I,
    ),
    "title": re.compile(r"^\s*#\s*(?P<value>[^\n]+)", re.M),
    "owner": re.compile(_FIELD_RE.format(label=r"(?:owner|process\s*owner|department)"), re.I),
    "process_area": re.compile(
        _FIELD_RE.format(label=r"(?:process\s*area|area|work\s*centre|work\s*center)"), re.I
    ),
}

_STATUS_MAP = {
    "approved": RevisionStatus.APPROVED,
    "released": RevisionStatus.APPROVED,
    "effective": RevisionStatus.APPROVED,
    "active": RevisionStatus.APPROVED,
    "draft": RevisionStatus.DRAFT,
    "in review": RevisionStatus.DRAFT,
    "pending approval": RevisionStatus.DRAFT,
    "pending": RevisionStatus.DRAFT,
    "unapproved": RevisionStatus.DRAFT,
    "superseded": RevisionStatus.SUPERSEDED,
    "obsolete": RevisionStatus.SUPERSEDED,
    "withdrawn": RevisionStatus.ARCHIVED,
    "archived": RevisionStatus.ARCHIVED,
    "retired": RevisionStatus.ARCHIVED,
}

_REV_CLEAN = re.compile(r"^(?:rev(?:ision)?\.?\s*)?", re.I)


def _EMPHASIS_SAFE(value: str) -> str:
    return normalize_whitespace(re.sub(r"[*_`~]+", " ", value)).strip(" .;,:-")


@dataclass
class SopMetadata:
    sop_code: str | None = None
    title: str | None = None
    revision_code: str | None = None
    status: str = RevisionStatus.UNKNOWN
    status_raw: str | None = None
    effective_date: date | None = None
    expiry_date: date | None = None
    approved_by: str | None = None
    approval_date: date | None = None
    supersedes_revision_code: str | None = None
    superseded_by_revision_code: str | None = None
    applicability: dict[str, list[str]] = field(default_factory=dict)
    owner: str | None = None
    process_area: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "sop_code": self.sop_code,
            "title": self.title,
            "revision_code": self.revision_code,
            "status": self.status,
            "status_raw": self.status_raw,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "approved_by": self.approved_by,
            "approval_date": self.approval_date.isoformat() if self.approval_date else None,
            "supersedes_revision_code": self.supersedes_revision_code,
            "superseded_by_revision_code": self.superseded_by_revision_code,
            "applicability": self.applicability,
            "owner": self.owner,
            "process_area": self.process_area,
            "warnings": self.warnings,
        }


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    candidate = normalize_whitespace(value).strip(" .;,")
    if not candidate or candidate.lower() in {"n/a", "na", "none", "tbd", "unknown", "-"}:
        return None
    try:
        return date_parser.parse(candidate, dayfirst=False, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def _clean_revision(value: str | None) -> str | None:
    if not value:
        return None
    v = _EMPHASIS_SAFE(value)
    v = _REV_CLEAN.sub("", v).strip()
    v = v.split("(")[0].strip()
    return v or None


def parse_applicability(value: str | None) -> dict[str, list[str]]:
    """Parse ``Lines: L1, L2 | Products: WID-100 | Sites: Plant A``."""
    result: dict[str, list[str]] = {}
    if not value:
        return result
    for part in re.split(r"[|;]", value):
        if ":" in part:
            key, _, raw = part.partition(":")
            key_norm = normalize_whitespace(key).lower().rstrip("s")
            items = [normalize_whitespace(v) for v in re.split(r"[,/]", raw) if normalize_whitespace(v)]
            if items:
                result[f"{key_norm}s"] = items
        else:
            free = normalize_whitespace(part)
            if free:
                result.setdefault("notes", []).append(free)
    return result


def extract_sop_metadata(text: str, *, fallback_filename: str | None = None) -> SopMetadata:
    """Best-effort metadata extraction; unknowns remain unknown."""
    meta = SopMetadata()
    head = text[:4000]  # metadata lives in the header block

    if (m := _PATTERNS["sop_code"].search(head)) or (
        fallback_filename and (m := _PATTERNS["sop_code"].search(fallback_filename))
    ):
        meta.sop_code = re.sub(r"[_\s]", "-", m.group("code")).upper()

    if m := _PATTERNS["title"].search(head):
        title = clean_field_value(m.group("value")) or ""
        title = re.sub(r"^SOP[-_ ]?[A-Z]{2,6}[-_ ]?\d{2,5}\s*[—–\-:]\s*", "", title, flags=re.I)
        meta.title = title or None

    if m := _PATTERNS["revision"].search(head):
        meta.revision_code = _clean_revision(clean_field_value(m.group("value")))
    if (
        not meta.revision_code
        and fallback_filename
        and (m := re.search(r"(?:rev|r|v)[-_ ]?(\d+[A-Za-z]?|[A-Za-z])\b", fallback_filename, re.I))
    ):
        meta.revision_code = m.group(1).upper()
    if not meta.revision_code:
        meta.warnings.append("Revision identifier not found in document.")

    if m := _PATTERNS["status"].search(head):
        raw = clean_field_value(m.group("value")) or ""
        meta.status_raw = raw
        key = raw.lower()
        mapped = None
        for needle, mapped_status in _STATUS_MAP.items():
            if needle in key:
                mapped = mapped_status
                break
        if mapped is None:
            meta.status = RevisionStatus.UNKNOWN
            meta.warnings.append(f"Unrecognised approval status '{raw}'; treated as unknown.")
        else:
            meta.status = mapped
    else:
        meta.warnings.append("Approval status not stated in document.")

    meta.effective_date = _parse_date(_match_value(head, "effective"))
    meta.expiry_date = _parse_date(_match_value(head, "expiry"))
    meta.approval_date = _parse_date(_match_value(head, "approval_date"))

    if v := _match_value(head, "approved_by"):
        cleaned = normalize_whitespace(v).strip(" .;,")
        if cleaned.lower() in {"pending", "n/a", "na", "none", "tbd", "-", "not approved"}:
            meta.warnings.append(f"Approver field is '{cleaned}': approval cannot be confirmed.")
            if meta.status == RevisionStatus.APPROVED:
                meta.status = RevisionStatus.UNKNOWN
                meta.warnings.append(
                    "Status claims approved but no approver is recorded; status downgraded to unknown."
                )
        else:
            meta.approved_by = cleaned
    elif meta.status == RevisionStatus.APPROVED:
        meta.warnings.append("Status is 'approved' but no approver is recorded.")

    meta.supersedes_revision_code = _clean_revision(_match_value(head, "supersedes"))
    meta.superseded_by_revision_code = _clean_revision(_match_value(head, "superseded_by"))
    meta.applicability = parse_applicability(_match_value(head, "applicability"))
    if v := _match_value(head, "owner"):
        meta.owner = normalize_whitespace(v).strip(" .;,")
    if v := _match_value(head, "process_area"):
        meta.process_area = normalize_whitespace(v).strip(" .;,")

    if meta.effective_date and meta.expiry_date and meta.expiry_date < meta.effective_date:
        meta.warnings.append("Expiry date precedes the effective date.")
    if meta.status == RevisionStatus.APPROVED and not meta.effective_date:
        meta.warnings.append("Approved document has no effective date; applicability window is undetermined.")
    return meta


_EMPHASIS = re.compile(r"[*_`~]+")


def clean_field_value(value: str | None) -> str | None:
    """Strip markdown emphasis/decoration left over from DOCX or Markdown sources."""
    if value is None:
        return None
    cleaned = _EMPHASIS.sub(" ", value)
    cleaned = normalize_whitespace(cleaned).strip(" .;,:-")
    return cleaned or None


def _match_value(text: str, key: str) -> str | None:
    m = _PATTERNS[key].search(text)
    if not m:
        return None
    return clean_field_value(m.group("value"))
