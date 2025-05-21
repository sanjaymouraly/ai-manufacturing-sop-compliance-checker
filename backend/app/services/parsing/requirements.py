"""Deterministic requirement extraction from parsed SOP sections.

Every requirement keeps the verbatim source text plus offsets, so a finding can
always quote the SOP exactly. Obligation strength, parameter constraints and
record-keeping duties are derived with explicit, reviewable rules — an LLM may
*augment* this later but never replaces it (see ``services/audit/pipeline.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.enums import Obligation
from app.services.parsing.structure import ParsedSection
from app.utils.text import normalize_text, normalize_whitespace
from app.utils.units import canonical_unit, dimension_of, is_known_unit

# --------------------------------------------------------------------------- #
# Lexicons (explicit and auditable)
# --------------------------------------------------------------------------- #
PROHIBITION_RE = re.compile(
    r"\b(shall|must|may|should|do|does|is|are)\s+not\b|\bnever\b|\bprohibited\b|\bforbidden\b", re.I
)
# "shall not exceed 28 °C" is a numeric *limit*, not a prohibited action.
LIMIT_NEGATION_RE = re.compile(
    r"\b(?:shall|must|should|may|is|are|do|does)\s+not\s+"
    r"(?:exceed|be\s+(?:more|less|greater|lower|higher)\s+than|fall\s+below|drop\s+below|go\s+(?:above|below))\b",
    re.I,
)
MANDATORY_RE = re.compile(
    r"\b(shall|must|is\s+required\s+to|are\s+required\s+to|required\s+to|mandatory)\b", re.I
)
OPTIONAL_RE = re.compile(
    r"\b(may|optional(ly)?|if\s+desired|at\s+the\s+operator'?s?\s+discretion|recommended|preferred|where\s+available)\b",
    re.I,
)
CONDITIONAL_RE = re.compile(
    r"^\s*(if|when|where|unless|in\s+case|should\s+the|for\s+products|whenever|on\s+lines?)\b|\b(if\s+applicable|as\s+applicable|where\s+applicable|if\s+required|when\s+required|as\s+needed)\b",
    re.I,
)
RECORD_RE = re.compile(
    r"\b(record|document|log|sign|initial|attach|photograph|capture|enter|register|retain)\b", re.I
)
INFORMATIVE_RE = re.compile(
    r"^\s*(note|caution|warning|purpose|scope|reference|definition|revision\s+history|this\s+procedure|the\s+purpose)\b",
    re.I,
)

IMPERATIVE_VERBS = {
    "verify",
    "check",
    "inspect",
    "measure",
    "record",
    "torque",
    "tighten",
    "clean",
    "install",
    "remove",
    "apply",
    "confirm",
    "calibrate",
    "label",
    "seal",
    "scan",
    "weigh",
    "test",
    "document",
    "ensure",
    "perform",
    "complete",
    "set",
    "adjust",
    "connect",
    "disconnect",
    "photograph",
    "sign",
    "review",
    "sample",
    "purge",
    "flush",
    "attach",
    "position",
    "mount",
    "dispose",
    "quarantine",
    "release",
    "package",
    "count",
    "log",
    "lubricate",
    "replace",
    "preheat",
    "heat",
    "cool",
    "hold",
    "maintain",
    "mix",
    "weld",
    "press",
    "fill",
    "drain",
    "isolate",
    "lock",
    "tag",
    "wipe",
    "rinse",
    "dry",
    "load",
    "unload",
    "start",
    "stop",
    "monitor",
    "calculate",
    "report",
    "notify",
    "escalate",
    "store",
    "transfer",
    "assemble",
}

# Words that look like a leading verb but never describe a physical action; used by the
# generic fallback below so that unseen domain verbs ("deburr", "anneal") still resolve.
NON_VERB_LEADS = {
    "the",
    "a",
    "an",
    "all",
    "any",
    "each",
    "if",
    "when",
    "where",
    "after",
    "before",
    "operator",
    "operators",
    "technician",
    "this",
    "that",
    "these",
    "those",
    "it",
    "there",
    "no",
    "not",
    "only",
    "do",
    "does",
    "for",
    "in",
    "on",
    "at",
    "to",
    "as",
    "per",
    "use",
}

PARAMETER_KEYWORDS: list[tuple[str, str]] = [
    (r"\btorque\b", "torque"),
    (r"\btemperature|temp\b", "temperature"),
    (r"\bpressure\b", "pressure"),
    (r"\bvacuum\b", "vacuum"),
    (r"\bhumidity\b", "humidity"),
    (r"\bweight|mass\b", "mass"),
    (r"\bgap|clearance\b", "clearance"),
    (r"\bthickness\b", "thickness"),
    (r"\blength|depth|height|diameter|width\b", "dimension"),
    (r"\bspeed|rpm\b", "speed"),
    (r"\bvoltage\b", "voltage"),
    (r"\bcurrent\b", "current"),
    (r"\bresistance\b", "resistance"),
    (r"\bflow\s*rate|flow\b", "flow_rate"),
    (r"\bdwell|cure|hold|dry|wait|soak|duration|time\b", "duration"),
    (r"\bvolume\b", "volume"),
    (r"\bquantity|count|qty\b", "quantity"),
    (r"\btension\b", "tension"),
    (r"\bconcentration\b", "concentration"),
]

_UNIT_TOKEN = r"(?:°\s?[CFK]|%|[A-Za-zµ][A-Za-z·.\-]{0,9})"
_NUM = r"-?\d+(?:[.,]\d+)?"

_CONSTRAINT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "pm",
        re.compile(
            rf"(?P<nominal>{_NUM})\s*(?P<unit>{_UNIT_TOKEN})?\s*(?:±|\+/-|\+-)\s*(?P<tol>{_NUM})\s*(?P<tolunit>{_UNIT_TOKEN})?"
        ),
    ),
    (
        "between",
        re.compile(
            rf"\b(?:between|from)\s+(?P<lo>{_NUM})\s*(?P<lounit>{_UNIT_TOKEN})?\s*(?:and|to|[–—-])\s*(?P<hi>{_NUM})\s*(?P<unit>{_UNIT_TOKEN})?",
            re.I,
        ),
    ),
    (
        "range",
        re.compile(rf"(?P<lo>{_NUM})\s*(?:–|—|-|to)\s*(?P<hi>{_NUM})\s*(?P<unit>{_UNIT_TOKEN})\b", re.I),
    ),
    (
        "ge",
        re.compile(
            rf"\b(?:at\s+least|minimum(?:\s+of)?|min\.?|no\s+less\s+than|not\s+less\s+than|≥|>=|greater\s+than\s+or\s+equal\s+to)\s*(?P<v>{_NUM})\s*(?P<unit>{_UNIT_TOKEN})?",
            re.I,
        ),
    ),
    (
        "le",
        re.compile(
            rf"\b(?:at\s+most|maximum(?:\s+of)?|max\.?|no\s+more\s+than|not\s+more\s+than|not\s+exceed(?:ing)?|must\s+not\s+exceed|≤|<=|less\s+than\s+or\s+equal\s+to)\s*(?P<v>{_NUM})\s*(?P<unit>{_UNIT_TOKEN})?",
            re.I,
        ),
    ),
    (
        "gt",
        re.compile(
            rf"\b(?:greater\s+than|more\s+than|above|exceeds?|>)\s*(?P<v>{_NUM})\s*(?P<unit>{_UNIT_TOKEN})?",
            re.I,
        ),
    ),
    ("lt", re.compile(rf"\b(?:less\s+than|below|under|<)\s*(?P<v>{_NUM})\s*(?P<unit>{_UNIT_TOKEN})?", re.I)),
    (
        "eq",
        re.compile(
            rf"\b(?:to|at|of|=|equal\s+to|set\s+to)\s+(?P<v>{_NUM})\s*(?P<unit>{_UNIT_TOKEN})\b", re.I
        ),
    ),
]

AMBIGUITY_TERMS = [
    "as appropriate",
    "as necessary",
    "if needed",
    "adequate",
    "sufficient",
    "suitable",
    "reasonable",
    "approximately",
    "about",
    "etc",
    "and/or",
    "where possible",
    "promptly",
    "clean enough",
    "normal",
    "standard practice",
    "as applicable",
]


@dataclass
class ExtractedRequirement:
    req_key: str
    seq: int
    text: str
    normalized_text: str
    obligation: str
    section_path: str
    page: int
    char_start: int
    char_end: int
    step_number: str | None = None
    action_verb: str | None = None
    condition_text: str | None = None
    requires_record: bool = False
    record_field: str | None = None
    parameter_name: str | None = None
    comparator: str | None = None
    value_min: float | None = None
    value_max: float | None = None
    nominal: float | None = None
    tolerance: float | None = None
    unit: str | None = None
    confidence: float = 1.0
    extraction_method: str = "rule"
    ambiguity_notes: list[str] = field(default_factory=list)
    section_id_hint: int | None = None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def detect_parameter_name(text: str) -> str | None:
    lowered = text.lower()
    for pattern, name in PARAMETER_KEYWORDS:
        if re.search(pattern, lowered):
            return name
    return None


def extract_constraint(text: str) -> dict[str, object] | None:
    """Find the first numeric constraint in a requirement sentence."""
    for kind, pattern in _CONSTRAINT_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        groups = m.groupdict()
        if kind == "pm":
            nominal = _to_float(groups.get("nominal"))
            tol = _to_float(groups.get("tol"))
            unit = groups.get("unit") or groups.get("tolunit")
            if nominal is None or tol is None:
                continue
            return {
                "comparator": "==",
                "nominal": nominal,
                "tolerance": tol,
                "value_min": nominal,
                "value_max": nominal,
                "unit": _clean_unit(unit),
                "span": m.span(),
            }
        if kind in {"between", "range"}:
            lo, hi = _to_float(groups.get("lo")), _to_float(groups.get("hi"))
            unit = groups.get("unit") or groups.get("lounit")
            if lo is None or hi is None or hi < lo:
                continue
            if kind == "range" and not _clean_unit(unit):
                continue
            return {
                "comparator": "between",
                "value_min": lo,
                "value_max": hi,
                "nominal": (lo + hi) / 2,
                "unit": _clean_unit(unit),
                "span": m.span(),
            }
        value = _to_float(groups.get("v"))
        unit = _clean_unit(groups.get("unit"))
        if value is None:
            continue
        if kind == "eq" and unit is None:
            continue
        mapping = {"ge": ">=", "le": "<=", "gt": ">", "lt": "<", "eq": "=="}
        comparator = mapping[kind]
        return {
            "comparator": comparator,
            "value_min": value if comparator in {">=", ">", "==", "!="} else None,
            "value_max": value if comparator in {"<=", "<"} else None,
            "nominal": value,
            "tolerance": 0.0 if comparator == "==" else None,
            "unit": unit,
            "span": m.span(),
        }
    return None


def _clean_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    u = unit.strip().strip(".,;:")
    if not u:
        return None
    # reject words that are not units (e.g. "to", "and")
    if not is_known_unit(u) or canonical_unit(u) is None:
        return None
    if dimension_of(u) is None:
        return None
    return u


def classify_obligation(text: str, kind: str) -> tuple[str, str | None]:
    """Return ``(obligation, condition_text)``."""
    stripped = text.strip()
    condition = None
    if m := CONDITIONAL_RE.search(stripped):
        clause = stripped[m.start() :]
        condition = normalize_whitespace(clause.split(",")[0])[:200]

    is_limit_phrase = bool(LIMIT_NEGATION_RE.search(stripped))
    if (
        not is_limit_phrase
        and PROHIBITION_RE.search(stripped)
        and re.search(
            r"\b(shall|must|may|should)\s+not\b|\bnever\b|\bprohibited\b|\bforbidden\b", stripped, re.I
        )
    ):
        return Obligation.PROHIBITED, condition
    if is_limit_phrase:
        return Obligation.MANDATORY, condition
    if INFORMATIVE_RE.match(stripped) and not MANDATORY_RE.search(stripped):
        return Obligation.INFORMATIVE, condition
    if OPTIONAL_RE.search(stripped) and not MANDATORY_RE.search(stripped):
        return Obligation.OPTIONAL, condition
    if condition:
        return Obligation.CONDITIONAL, condition
    if MANDATORY_RE.search(stripped):
        return Obligation.MANDATORY, condition
    first_word = normalize_text(stripped).split(" ")[0] if stripped else ""
    if kind == "step" and first_word in IMPERATIVE_VERBS:
        return Obligation.MANDATORY, condition
    if kind == "step":
        return Obligation.MANDATORY, condition
    return Obligation.INFORMATIVE, condition


def _action_verb(text: str) -> str | None:
    tokens = [t for t in normalize_text(text).split(" ") if t]
    for token in tokens[:6]:
        if token in IMPERATIVE_VERBS:
            return token
    for token in tokens:
        if token in IMPERATIVE_VERBS:
            return token
    # Fallback: SOP steps are overwhelmingly written as leading imperatives, so an unknown
    # leading word is more useful as an action label than None. Kept conservative - only a
    # plausible single alphabetic word that is not a known non-verb lead.
    if tokens:
        lead = tokens[0]
        if lead.isalpha() and 3 <= len(lead) <= 14 and lead not in NON_VERB_LEADS:
            return lead
    return None


def _record_field(text: str) -> str | None:
    m = re.search(
        r"\brecord(?:\s+the)?\s+([a-z][a-z \-]{2,40}?)(?:\s+(?:in|on|to|using|and)\b|[.,;]|$)", text, re.I
    )
    if m:
        return normalize_whitespace(m.group(1)).lower()
    return None


def _is_requirement_line(section: ParsedSection) -> bool:
    text = section.text.strip()
    if len(text) < 12:
        return False
    if section.kind == "heading":
        return False
    if section.kind == "table":
        return bool(re.search(r"\d", text)) and "|" in text
    if section.kind == "step":
        return True
    if MANDATORY_RE.search(text) or PROHIBITION_RE.search(text):
        return True
    return bool(section.kind == "list" and _action_verb(text))


def extract_requirements(
    sections: list[ParsedSection],
    *,
    sop_code: str,
    revision_code: str,
) -> list[ExtractedRequirement]:
    """Deterministically derive requirements from parsed sections."""
    requirements: list[ExtractedRequirement] = []
    seq = 0
    prefix = f"{sop_code}:{revision_code}" if sop_code else revision_code

    for index, section in enumerate(sections):
        if not _is_requirement_line(section):
            continue
        text = normalize_whitespace(section.text)
        obligation, condition = classify_obligation(text, section.kind)
        if obligation == Obligation.INFORMATIVE and section.kind != "step":
            continue

        seq += 1
        req_key = f"{prefix}:R{seq:03d}"
        constraint = extract_constraint(text) or {}
        parameter_name = None
        if constraint:
            unit_value = constraint.get("unit")
            parameter_name = detect_parameter_name(text) or (
                dimension_of(str(unit_value)) if unit_value else None
            )

        ambiguity = [term for term in AMBIGUITY_TERMS if term in text.lower()]
        notes = [f"Ambiguous phrasing: '{term}'" for term in ambiguity]
        if constraint and not constraint.get("unit") and constraint.get("comparator") != "==":
            notes.append("Numeric limit without an explicit unit.")

        confidence = 1.0
        if ambiguity:
            confidence -= 0.15 * min(2, len(ambiguity))
        if section.kind not in {"step", "table"}:
            confidence -= 0.05
        requires_record = bool(RECORD_RE.search(text))

        requirements.append(
            ExtractedRequirement(
                req_key=req_key,
                seq=seq,
                text=text,
                normalized_text=normalize_text(text),
                obligation=obligation,
                section_path=section.section_path,
                page=section.page,
                char_start=section.char_start,
                char_end=section.char_end,
                step_number=section.step_number,
                action_verb=_action_verb(text),
                condition_text=condition,
                requires_record=requires_record,
                record_field=_record_field(text) if requires_record else None,
                parameter_name=parameter_name,
                comparator=constraint.get("comparator"),  # type: ignore[arg-type]
                value_min=constraint.get("value_min"),  # type: ignore[arg-type]
                value_max=constraint.get("value_max"),  # type: ignore[arg-type]
                nominal=constraint.get("nominal"),  # type: ignore[arg-type]
                tolerance=constraint.get("tolerance"),  # type: ignore[arg-type]
                unit=constraint.get("unit"),  # type: ignore[arg-type]
                confidence=round(max(0.3, confidence), 3),
                ambiguity_notes=notes,
                section_id_hint=index,
            )
        )
    return requirements
