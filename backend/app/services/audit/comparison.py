"""Deterministic expected-vs-observed comparison.

This module is the heart of the auditor and runs **before and independently of
any LLM**. Simple verifiable rules (numeric limits, required fields, ordering,
duplication) are decided here; the LLM may only add explanatory narrative or
flag *additional* candidate issues that are then re-verified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.models.entities import ProcessReport, ReportEvent, Requirement, SopRevision
from app.models.enums import (
    Detector,
    EventStatus,
    EvidenceSufficiency,
    FindingCategory,
    FindingClassification,
    Obligation,
    Severity,
)
from app.services.audit.matching import EvidenceMatch, find_duplicate_executions, unmatched_events
from app.utils import units as unit_utils
from app.utils.text import SYNONYMS, normalize_text, stem, tokenize, truncate

COMPLETION_CLAIM_RE = re.compile(
    r"\b(all\s+(?:steps|checks|items|requirements)\s+(?:were\s+)?(?:completed|performed|done|verified)"
    r"|fully\s+compliant|no\s+deviations?\b|100%\s+complete|everything\s+(?:was\s+)?(?:completed|done)"
    r"|procedure\s+followed\s+in\s+full|completed\s+per\s+sop)\b",
    re.I,
)
NEGATION_RE = re.compile(
    r"\b(not|no|without|never|failed\s+to|unable\s+to|could\s+not|couldn'?t|skipped)\b", re.I
)


@dataclass
class CitationDraft:
    source_type: str  # sop | report
    document_label: str
    quote: str
    char_start: int
    char_end: int
    revision_id: int | None = None
    report_id: int | None = None
    section_path: str | None = None
    page: int | None = None
    line_ref: str | None = None


@dataclass
class FindingDraft:
    category: str
    classification: str
    severity: str
    title: str
    expected: str
    observed: str
    explanation: str
    suggested_action: str
    requirement: Requirement | None = None
    evidence_sufficiency: str = EvidenceSufficiency.SUFFICIENT
    confidence: float = 1.0
    detector: str = Detector.DETERMINISTIC
    citations: list[CitationDraft] = field(default_factory=list)
    uncertainty_notes: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    conflict_id: int | None = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _sop_citation(revision: SopRevision, requirement: Requirement) -> CitationDraft:
    label = (
        f"{revision.document.sop_code} {revision.revision_code}"
        if revision.document
        else revision.revision_code
    )
    return CitationDraft(
        source_type="sop",
        document_label=label,
        quote=requirement.text,
        char_start=requirement.char_start,
        char_end=requirement.char_end,
        revision_id=revision.id,
        section_path=requirement.section_path,
        page=requirement.page,
    )


def _report_citation(report: ProcessReport, event: ReportEvent) -> CitationDraft:
    return CitationDraft(
        source_type="report",
        document_label=report.report_code,
        quote=event.text,
        char_start=event.char_start,
        char_end=event.char_end,
        report_id=report.id,
        page=event.page,
        line_ref=event.line_ref,
    )


def _describe_requirement(requirement: Requirement) -> str:
    if requirement.comparator:
        unit = f" {requirement.unit}" if requirement.unit else ""
        if requirement.comparator == "between":
            return f"{requirement.parameter_name or 'value'} within {requirement.value_min:g}–{requirement.value_max:g}{unit}"
        if requirement.comparator == "==":
            tol = f" ±{requirement.tolerance:g}" if requirement.tolerance else ""
            return f"{requirement.parameter_name or 'value'} = {requirement.value_min:g}{unit}{tol}"
        bound = requirement.value_min if requirement.value_min is not None else requirement.value_max
        if bound is not None:
            return f"{requirement.parameter_name or 'value'} {requirement.comparator} {bound:g}{unit}"
    return truncate(requirement.text, 200)


def _acceptance_band(requirement: Requirement) -> tuple[float, float] | None:
    """The finite acceptance interval of a requirement, if it has one."""
    if (
        requirement.comparator == "between"
        and requirement.value_min is not None
        and requirement.value_max is not None
    ):
        return float(requirement.value_min), float(requirement.value_max)
    if requirement.comparator == "==" and requirement.value_min is not None:
        tol = float(requirement.tolerance or 0.0)
        if tol > 0:
            nominal = float(requirement.value_min)
            return nominal - tol, nominal + tol
    return None


def _severity_for_deviation(result: unit_utils.ComparisonResult, requirement: Requirement) -> str:
    """Grade a parameter deviation by how far outside the *tolerance band* it falls.

    Percentage-of-nominal is the wrong yardstick for tight tolerances: 25.5 mm against a
    25.0 mm ± 0.3 mm spec is only 2 % off nominal but it overshoots the whole band by two
    thirds, which a quality engineer would never call "minor". Where the requirement has a
    finite band we grade on excess / half-band; open-ended limits (">= 60 %") keep the
    percentage model because no band width exists.
    """
    if requirement.obligation == Obligation.PROHIBITED:
        return Severity.CRITICAL
    excess = abs(result.deviation or 0.0)
    band = _acceptance_band(requirement)
    if band is not None:
        lo, hi = band
        half_band = (hi - lo) / 2.0
        if half_band > 0:
            ratio = excess / half_band
            if ratio >= 1.0:
                return Severity.CRITICAL
            if ratio >= 0.33:
                return Severity.MAJOR
            return Severity.MINOR
    pct = abs(result.deviation_pct or 0.0)
    if pct >= 20:
        return Severity.CRITICAL
    if pct >= 5:
        return Severity.MAJOR
    return Severity.MINOR


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Conditional step handling
# --------------------------------------------------------------------------- #
#: Words that negate a trigger condition when they sit just before the condition's
#: own keywords ("no damage found", "none observed", "free of cracks").
_NEGATION_MARKERS = frozenset(
    {"no", "not", "none", "never", "without", "nil", "free", "clear", "absent", "negative"}
)
#: Leading words of a condition clause that carry no matchable content.
_CONDITION_LEADS = frozenset({"if", "when", "where", "whenever", "wherever", "should", "in", "case"})
#: Conditions phrased this vaguely cannot be evaluated from the record at all.
_VAGUE_CONDITIONS = frozenset({"applicable", "necessary", "required", "appropriate", "needed"})
#: Record fields that name no specific datum - an explicit execution status satisfies them.
#: Tokens in a prohibition clause that describe the prohibition rather than the act.
_PROHIBITION_NOISE = frozenset(
    {
        "operator",
        "operators",
        "technician",
        "never",
        "not",
        "no",
        "prohibit",
        "prohibited",
        "forbidden",
        "under",
        "circumstance",
        "circumstances",
        "allow",
        "allowed",
        "permit",
        "permitted",
        "may",
        "shall",
        "must",
        "should",
        "step",
        "procedure",
    }
)
_GENERIC_RECORD_FIELDS = frozenset(
    {
        "result",
        "results",
        "outcome",
        "status",
        "check",
        "inspection",
        "pass",
        "fail",
        "ok",
        "nok",
        "conformance",
        "acceptance",
        "completion",
    }
)


#: Prefixes that invert the meaning of the word they are attached to.
_NEGATING_PREFIXES = ("un", "non", "in")


def _condition_tokens(condition_text: str | None) -> list[str]:
    """Ordered content tokens of a condition clause.

    Order matters: the final token is normally the predicate ("damaged" in "if the
    packaging is damaged", "found" in "if any defect is found"), and the predicate is what
    decides whether the condition actually occurred. The earlier tokens only identify the
    subject, and a subject mention on its own proves nothing.
    """
    if not condition_text:
        return []
    tokens = [t for t in tokenize(condition_text) if t not in _CONDITION_LEADS]
    return [t for t in tokens if len(t) >= 3]


def _negated_form_of(token: str, wanted: set[str]) -> str | None:
    """If ``token`` is a negated spelling of a wanted term, return that term.

    "undamaged" is not the absence of a match for "damaged" - it is a *negative
    observation* of it, which is the opposite of what a naive stem comparison concludes.
    """
    for prefix in _NEGATING_PREFIXES:
        if token.startswith(prefix) and len(token) > len(prefix) + 2:
            candidate = stem(SYNONYMS.get(token[len(prefix) :], token[len(prefix) :]))
            if candidate in wanted:
                return candidate
    return None


def _negated_near(tokens: list[str], index: int, window: int = 3) -> bool:
    start = max(0, index - window)
    return any(t in _NEGATION_MARKERS or t.startswith("un") for t in tokens[start:index])


def evaluate_condition(
    requirement: Requirement, report: ProcessReport, events: list[ReportEvent]
) -> tuple[str, str | None]:
    """Decide whether a conditional requirement's trigger actually occurred.

    Returns ``(state, evidence_text)`` where state is ``"triggered"``,
    ``"not_triggered"`` or ``"unknown"``. A conditional step whose trigger never
    occurred must not be reported as missing - that is the single largest source of
    false positives in SOPs that branch on inspection outcomes.
    """
    ordered = _condition_tokens(requirement.condition_text)
    wanted = set(ordered)
    if not wanted or wanted & _VAGUE_CONDITIONS:
        return "unknown", None
    predicate = ordered[-1]

    haystacks: list[str] = [e.text or "" for e in events]
    for extra in (report.raw_text or "").split("\n"):
        if extra.strip():
            haystacks.append(extra)

    best_state = "unknown"
    best_evidence: str | None = None
    for text in haystacks:
        # Negation analysis needs the *raw* token stream: stopword removal would delete
        # "no"/"not" - the very words that decide whether the condition occurred. The two
        # lists stay index-aligned so a hit can be tested against its own neighbourhood.
        raw = [t for t in normalize_text(text).split(" ") if t]
        if not raw:
            continue
        forms: list[str] = []
        forced_negative: list[bool] = []
        for token in raw:
            negated_term = _negated_form_of(token, wanted)
            if negated_term is not None:
                forms.append(negated_term)
                forced_negative.append(True)
            else:
                forms.append(stem(SYNONYMS.get(token, token)))
                forced_negative.append(False)
        hits = [i for i, f in enumerate(forms) if f in wanted]
        if not hits:
            continue
        coverage = len({forms[i] for i in hits}) / len(wanted)
        if coverage < 0.5:
            continue
        predicate_hits = [i for i in hits if forms[i] == predicate]
        if not predicate_hits:
            # Only the subject of the condition was mentioned ("packaging"), never the
            # predicate ("damaged"). That is not evidence either way.
            continue
        # Last-mention rule. A single sentence often states the purpose before the result
        # ("Inspected packaging for transport damage - no damage found"): the earlier
        # mention is the thing looked for, the final one is what was actually observed.
        # Deciding on the last occurrence matches how a reviewer reads the line.
        last = predicate_hits[-1]
        negated = forced_negative[last] or _negated_near(raw, last)
        state = "not_triggered" if negated else "triggered"
        # A positive observation anywhere outweighs a negative one: if any record says the
        # condition occurred, the conditional action is expected.
        if state == "triggered":
            return "triggered", truncate(text, 200)
        if best_state == "unknown":
            best_state, best_evidence = state, truncate(text, 200)
    return best_state, best_evidence


def check_missing_and_status(
    requirement: Requirement,
    match: EvidenceMatch,
    revision: SopRevision,
    report: ProcessReport,
    *,
    report_is_structured: bool,
    all_events: list[ReportEvent] | None = None,
) -> list[FindingDraft]:
    drafts: list[FindingDraft] = []
    expected = _describe_requirement(requirement)

    if not match.matched:
        if requirement.obligation == Obligation.OPTIONAL:
            return []
        if requirement.obligation == Obligation.INFORMATIVE:
            return []
        if requirement.obligation == Obligation.PROHIBITED:
            return []  # absence of a prohibited action is the desired state

        if requirement.obligation == Obligation.CONDITIONAL:
            state, evidence = evaluate_condition(requirement, report, all_events or [])
            if state == "not_triggered":
                # The record positively shows the trigger did not occur, so the step was
                # correctly not performed. Reported as an observation, never as a finding.
                drafts.append(
                    FindingDraft(
                        category=FindingCategory.COMPLIANT_OBSERVATION,
                        classification=FindingClassification.NO_DETECTED_DEVIATION,
                        severity=Severity.INFO,
                        title=(
                            f"Conditional step {requirement.step_number or requirement.req_key} "
                            "not applicable"
                        ),
                        expected=expected,
                        observed=f"Trigger condition recorded as not occurring: “{evidence}”.",
                        explanation=(
                            "The SOP only requires this step when "
                            f"{truncate(requirement.condition_text or 'a condition applies', 120)}. "
                            "The process report records that the condition did not occur, so the absence "
                            "of this step is consistent with the SOP. This is an observation, not a "
                            "statement of compliance."
                        ),
                        suggested_action="No action required unless the trigger evidence is disputed.",
                        requirement=requirement,
                        evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
                        confidence=0.6,
                        citations=[_sop_citation(revision, requirement)],
                        metrics={"condition_state": state, "best_match_score": match.score},
                    )
                )
                return drafts

            triggered = state == "triggered"
            drafts.append(
                FindingDraft(
                    category=FindingCategory.MISSING_STEP,
                    classification=(
                        FindingClassification.POSSIBLE_DEVIATION
                        if triggered and report_is_structured
                        else FindingClassification.INSUFFICIENT_EVIDENCE
                    ),
                    severity=Severity.MAJOR if triggered else Severity.MINOR,
                    title=(
                        f"Conditional step {requirement.step_number or requirement.req_key} "
                        + ("required but not recorded" if triggered else "not evidenced")
                    ),
                    expected=expected,
                    observed=(
                        f"Trigger condition appears to have occurred (“{evidence}”) but no corresponding "
                        "entry was found."
                        if triggered
                        else "No corresponding entry found in the process report."
                    ),
                    explanation=(
                        f"The SOP makes this step conditional ({truncate(requirement.condition_text or '', 120)}). "
                        + (
                            "The report records the trigger condition, so the conditional action was "
                            "required, but no record of it was found."
                            if triggered
                            else "The report contains no record of the step and no record of whether the "
                            "condition applied, so compliance cannot be determined from the available evidence."
                        )
                    ),
                    suggested_action=(
                        "Confirm whether the conditional action was performed and record the outcome."
                        if triggered
                        else "Confirm with the operator whether the trigger condition occurred and record the outcome."
                    ),
                    requirement=requirement,
                    evidence_sufficiency=(
                        EvidenceSufficiency.PARTIAL if triggered else EvidenceSufficiency.INSUFFICIENT
                    ),
                    confidence=0.65 if triggered else 0.5,
                    citations=[_sop_citation(revision, requirement)],
                    uncertainty_notes=(
                        [] if triggered else ["Condition applicability not stated in the report."]
                    ),
                    metrics={"condition_state": state, "best_match_score": match.score},
                )
            )
            return drafts

        classification = (
            FindingClassification.CONFIRMED_DEVIATION
            if report_is_structured
            else FindingClassification.POSSIBLE_DEVIATION
        )
        sufficiency = EvidenceSufficiency.SUFFICIENT if report_is_structured else EvidenceSufficiency.PARTIAL
        drafts.append(
            FindingDraft(
                category=FindingCategory.MISSING_STEP,
                classification=classification,
                severity=Severity.CRITICAL if requirement.comparator else Severity.MAJOR,
                title=f"Mandatory step {requirement.step_number or requirement.req_key} not recorded",
                expected=expected,
                observed="No matching execution record found in the process report.",
                explanation=(
                    f"The SOP requires: “{truncate(requirement.text, 200)}”. No entry in the process report "
                    f"matches this requirement (best evidence score {match.score:.2f}, below the "
                    "acceptance threshold)."
                    + (
                        ""
                        if report_is_structured
                        else " The report is unstructured, so extraction may be incomplete."
                    )
                ),
                suggested_action=(
                    "Confirm whether the step was performed and recorded elsewhere; if not performed, "
                    "raise a deviation record for quality review."
                ),
                requirement=requirement,
                evidence_sufficiency=sufficiency,
                confidence=0.9 if report_is_structured else 0.6,
                citations=[_sop_citation(revision, requirement)],
                uncertainty_notes=(
                    []
                    if report_is_structured
                    else ["Unstructured report: absence of evidence is weaker evidence."]
                ),
                metrics={"best_match_score": match.score, "alternatives": match.alternatives},
            )
        )
        return drafts

    event = match.event
    assert event is not None

    if event.status == EventStatus.SKIPPED and requirement.obligation == Obligation.MANDATORY:
        drafts.append(
            FindingDraft(
                category=FindingCategory.MISSING_STEP,
                classification=FindingClassification.CONFIRMED_DEVIATION,
                severity=Severity.CRITICAL,
                title=f"Mandatory step {requirement.step_number or requirement.req_key} recorded as skipped",
                expected=expected,
                observed=f"Report records status '{event.status}' for this step.",
                explanation=(
                    f"The SOP requires this step unconditionally, but the process report explicitly records it "
                    f"as skipped: “{truncate(event.text, 160)}”."
                ),
                suggested_action="Raise a deviation; determine product disposition before release.",
                requirement=requirement,
                citations=[_sop_citation(revision, requirement), _report_citation(report, event)],
                metrics={"match_score": match.score},
            )
        )
    elif event.status == EventStatus.FAILED:
        drafts.append(
            FindingDraft(
                category=FindingCategory.PARAMETER_DEVIATION
                if requirement.comparator
                else FindingCategory.MISSING_STEP,
                classification=FindingClassification.CONFIRMED_DEVIATION,
                severity=Severity.CRITICAL,
                title=f"Step {requirement.step_number or requirement.req_key} recorded as failed",
                expected=expected,
                observed=f"Report records a failed result: “{truncate(event.text, 160)}”.",
                explanation="The operator record reports a failure for a required step.",
                suggested_action="Investigate the failure, confirm rework/scrap disposition and record the outcome.",
                requirement=requirement,
                citations=[_sop_citation(revision, requirement), _report_citation(report, event)],
                metrics={"match_score": match.score},
            )
        )
    elif event.status == EventStatus.UNKNOWN and requirement.obligation == Obligation.MANDATORY:
        drafts.append(
            FindingDraft(
                category=FindingCategory.MISSING_RECORD,
                classification=FindingClassification.INSUFFICIENT_EVIDENCE,
                severity=Severity.MINOR,
                title=f"Step {requirement.step_number or requirement.req_key} has no completion status",
                expected=f"{expected} (with a recorded completion status)",
                observed=f"Entry found but status is not stated: “{truncate(event.text, 160)}”.",
                explanation=(
                    "An entry matching this requirement exists, but the report does not state whether the step "
                    "was completed. Completion cannot be confirmed from the record."
                ),
                suggested_action="Ask the operator to complete the status field for this step.",
                requirement=requirement,
                evidence_sufficiency=EvidenceSufficiency.PARTIAL,
                confidence=0.7,
                citations=[_sop_citation(revision, requirement), _report_citation(report, event)],
                uncertainty_notes=["Completion status missing in the source record."],
                metrics={"match_score": match.score},
            )
        )

    if requirement.obligation == Obligation.PROHIBITED and event.status in {
        EventStatus.COMPLETED,
        EventStatus.UNKNOWN,
    }:
        drafts.append(
            FindingDraft(
                category=FindingCategory.PROHIBITED_ACTION,
                classification=FindingClassification.CONFIRMED_DEVIATION
                if event.status == EventStatus.COMPLETED
                else FindingClassification.POSSIBLE_DEVIATION,
                severity=Severity.CRITICAL,
                title=f"Prohibited action appears in the execution record ({requirement.req_key})",
                expected=f"Prohibited: {truncate(requirement.text, 160)}",
                observed=truncate(event.text, 200),
                explanation="The SOP prohibits this action, but the report contains a matching execution entry.",
                suggested_action="Verify immediately with the operator and quarantine affected product if confirmed.",
                requirement=requirement,
                confidence=0.8,
                citations=[_sop_citation(revision, requirement), _report_citation(report, event)],
                metrics={"match_score": match.score},
            )
        )
    return drafts


def check_parameter(
    requirement: Requirement,
    match: EvidenceMatch,
    revision: SopRevision,
    report: ProcessReport,
) -> list[FindingDraft]:
    if not requirement.comparator or not match.matched:
        return []
    event = match.event
    assert event is not None
    expected = _describe_requirement(requirement)

    if event.value_num is None:
        if event.status in {EventStatus.SKIPPED, EventStatus.NOT_APPLICABLE}:
            return []
        return [
            FindingDraft(
                category=FindingCategory.MISSING_RECORD,
                classification=FindingClassification.INSUFFICIENT_EVIDENCE,
                severity=Severity.MAJOR,
                title=f"No measured value recorded for {requirement.parameter_name or 'required parameter'}",
                expected=expected,
                observed=f"Entry present without a numeric measurement: “{truncate(event.text, 160)}”.",
                explanation=(
                    "The SOP specifies a numeric acceptance limit, but the process report does not contain a "
                    "measured value for this step, so conformance cannot be calculated."
                ),
                suggested_action="Retrieve the measurement record or re-measure and document the result.",
                requirement=requirement,
                evidence_sufficiency=EvidenceSufficiency.INSUFFICIENT,
                confidence=0.75,
                citations=[_sop_citation(revision, requirement), _report_citation(report, event)],
                uncertainty_notes=["Numeric value absent from the report entry."],
                metrics={"match_score": match.score},
            )
        ]

    try:
        result = unit_utils.compare(
            observed=event.value_num,
            observed_unit=event.unit,
            comparator=requirement.comparator,
            value_min=requirement.value_min,
            value_max=requirement.value_max,
            expected_unit=requirement.unit,
            tolerance=requirement.tolerance,
        )
    except unit_utils.IncompatibleUnitsError as exc:
        return [
            FindingDraft(
                category=FindingCategory.UNIT_MISMATCH,
                classification=FindingClassification.POSSIBLE_DEVIATION,
                severity=Severity.MAJOR,
                title=f"Unit mismatch for {requirement.parameter_name or 'parameter'}",
                expected=expected,
                observed=f"{event.value_num:g} {event.unit or '(no unit)'}",
                explanation=(
                    f"The recorded unit cannot be reconciled with the SOP requirement: {exc}. "
                    "The value was not converted and conformance was not calculated — no assumption was made "
                    "about the intended unit."
                ),
                suggested_action=(
                    "Confirm the measurement unit used on the shop floor and correct the record or the SOP."
                ),
                requirement=requirement,
                evidence_sufficiency=EvidenceSufficiency.INSUFFICIENT,
                confidence=0.8,
                citations=[_sop_citation(revision, requirement), _report_citation(report, event)],
                uncertainty_notes=[str(exc)],
                metrics={
                    "observed_value": event.value_num,
                    "observed_unit": event.unit,
                    "match_score": match.score,
                },
            )
        ]

    metrics = {
        "observed_value": event.value_num,
        "observed_unit": event.unit,
        "observed_in_expected_unit": result.observed_in_expected_unit,
        "expected": result.expected_description,
        "deviation": result.deviation,
        "deviation_pct": result.deviation_pct,
        "converted": result.converted,
        "match_score": match.score,
    }

    if result.in_spec:
        return [
            FindingDraft(
                category=FindingCategory.COMPLIANT_OBSERVATION,
                classification=FindingClassification.NO_DETECTED_DEVIATION,
                severity=Severity.INFO,
                title=f"{requirement.parameter_name or 'Parameter'} within specification ({requirement.req_key})",
                expected=expected,
                observed=f"{event.value_num:g} {event.unit or ''}".strip(),
                explanation=(
                    f"Recorded value {event.value_num:g} {event.unit or ''} satisfies {result.expected_description}."
                    + (f" {result.note}." if result.note else "")
                    + " This is a recorded observation, not a certification of the process."
                ),
                suggested_action="No action required based on this parameter alone.",
                requirement=requirement,
                citations=[_sop_citation(revision, requirement), _report_citation(report, event)],
                metrics=metrics,
            )
        ]

    deviation_text = f"{result.deviation:+.4g} {requirement.unit or ''}".strip() + (
        f" ({result.deviation_pct:+.2f}%)" if result.deviation_pct is not None else ""
    )
    return [
        FindingDraft(
            category=FindingCategory.PARAMETER_DEVIATION,
            classification=FindingClassification.CONFIRMED_DEVIATION,
            severity=_severity_for_deviation(result, requirement),
            title=f"{requirement.parameter_name or 'Parameter'} outside SOP limits ({requirement.req_key})",
            expected=expected,
            observed=f"{event.value_num:g} {event.unit or ''}".strip(),
            explanation=(
                f"The SOP requires {result.expected_description}. The report records "
                f"{event.value_num:g} {event.unit or ''}"
                + (f" ({result.note})" if result.converted else "")
                + f", a deviation of {deviation_text}. Calculated deterministically from the recorded value."
            ),
            suggested_action=(
                "Raise a deviation record, assess product impact and determine whether rework or quarantine "
                "is required."
            ),
            requirement=requirement,
            citations=[_sop_citation(revision, requirement), _report_citation(report, event)],
            metrics=metrics,
        )
    ]


def check_record_keeping(
    requirement: Requirement, match: EvidenceMatch, revision: SopRevision, report: ProcessReport
) -> list[FindingDraft]:
    if not requirement.requires_record or not match.matched:
        return []
    event = match.event
    assert event is not None
    missing: list[str] = []
    if requirement.comparator and event.value_num is None:
        return []  # already covered by the parameter check
    if not event.operator and not report.operator:
        missing.append("operator identity")
    if not event.timestamp_text and not event.recorded_at:
        missing.append("timestamp")
    if requirement.record_field and not (event.value_text or event.value_num is not None):
        field_tokens = {t for t in tokenize(requirement.record_field) if len(t) > 2}
        generic_outcome = bool(field_tokens) and field_tokens <= _GENERIC_RECORD_FIELDS
        # "Record the result" is satisfied by an explicit execution status: the structured
        # status field *is* the recorded result. Only a genuinely unknown status leaves the
        # record incomplete.
        if generic_outcome and event.status != EventStatus.UNKNOWN:
            pass
        elif not field_tokens <= set(tokenize(event.text or "")):
            missing.append(f"recorded '{requirement.record_field}'")
    if not missing:
        return []
    return [
        FindingDraft(
            category=FindingCategory.MISSING_RECORD,
            classification=FindingClassification.POSSIBLE_DEVIATION,
            severity=Severity.MINOR,
            title=f"Incomplete record for step {requirement.step_number or requirement.req_key}",
            expected=f"{truncate(requirement.text, 160)} (record required)",
            observed=f"Recorded entry is missing: {', '.join(missing)}.",
            explanation=(
                "The SOP requires this step to be documented, but the corresponding record is incomplete. "
                "Traceability of the execution is therefore limited."
            ),
            suggested_action="Complete the record with the missing attributes and confirm with the operator.",
            requirement=requirement,
            evidence_sufficiency=EvidenceSufficiency.PARTIAL,
            confidence=0.7,
            citations=[_sop_citation(revision, requirement), _report_citation(report, event)],
            metrics={"missing_fields": missing, "match_score": match.score},
        )
    ]


def check_prohibited_actions(
    requirements: list[Requirement],
    events: list[ReportEvent],
    revision: SopRevision,
    report: ProcessReport,
) -> tuple[list[FindingDraft], set[int]]:
    """Did a prohibited action happen at all?

    Prohibitions cannot be handled by step matching: an operator never records
    "I did not reuse the fasteners", and when they *do* reuse them the entry carries no
    step number from the prohibition clause. So instead of asking "which entry corresponds
    to this requirement", this check asks "does any entry describe the forbidden action,
    stated affirmatively". Returns the drafts plus the event ids they consumed.
    """
    drafts: list[FindingDraft] = []
    consumed: set[int] = set()
    prohibitions = [r for r in requirements if r.obligation == Obligation.PROHIBITED]
    if not prohibitions:
        return drafts, consumed

    for requirement in prohibitions:
        # Content of the forbidden act, with the negation itself removed.
        wanted = {t for t in tokenize(requirement.text) if t not in _PROHIBITION_NOISE and len(t) >= 3}
        if not wanted:
            continue
        for event in events:
            if event.status in {EventStatus.SKIPPED, EventStatus.NOT_APPLICABLE}:
                continue
            tokens = set(tokenize(event.text or ""))
            if not tokens:
                continue
            overlap = wanted & tokens
            coverage = len(overlap) / len(wanted)
            if coverage < 0.6:
                continue
            if _NEGATION_MARKERS & tokens:
                continue  # "did not reuse ..." is compliance, not a violation
            consumed.add(event.id)
            drafts.append(
                FindingDraft(
                    category=FindingCategory.PROHIBITED_ACTION,
                    classification=FindingClassification.CONFIRMED_DEVIATION,
                    severity=Severity.CRITICAL,
                    title=(
                        f"Prohibited action recorded against step "
                        f"{requirement.step_number or requirement.req_key}"
                    ),
                    expected=f"Prohibited: {truncate(requirement.text, 160)}",
                    observed=truncate(event.text or "", 180),
                    explanation=(
                        f"The SOP explicitly prohibits this action: “{truncate(requirement.text, 200)}”. "
                        "The process report records an activity that describes the prohibited action "
                        f"({len(overlap)} of {len(wanted)} key terms matched, no negation present)."
                    ),
                    suggested_action=(
                        "Quarantine the affected output pending review and raise a deviation record."
                    ),
                    requirement=requirement,
                    evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
                    confidence=0.75,
                    citations=[_sop_citation(revision, requirement), _report_citation(report, event)],
                    detector=Detector.DETERMINISTIC,
                    metrics={"term_coverage": round(coverage, 3)},
                )
            )
    return drafts, consumed


def check_sequence(
    requirements: list[Requirement],
    matches: dict[int, EvidenceMatch],
    revision: SopRevision,
    report: ProcessReport,
) -> list[FindingDraft]:
    """Detect executed-out-of-order mandatory steps (deterministic inversion count)."""
    ordered: list[tuple[Requirement, EvidenceMatch]] = [
        (r, matches[r.id])
        for r in requirements
        if matches.get(r.id) and matches[r.id].matched and r.obligation == Obligation.MANDATORY
    ]
    ordered.sort(key=lambda item: item[0].seq)
    drafts: list[FindingDraft] = []
    for i in range(len(ordered)):
        req_i, match_i = ordered[i]
        for j in range(i + 1, len(ordered)):
            req_j, match_j = ordered[j]
            assert match_i.event and match_j.event
            if match_j.event.seq < match_i.event.seq:
                drafts.append(
                    FindingDraft(
                        category=FindingCategory.OUT_OF_SEQUENCE,
                        classification=FindingClassification.CONFIRMED_DEVIATION,
                        severity=Severity.MAJOR,
                        title=(
                            f"Steps executed out of sequence: {req_j.step_number or req_j.req_key} before "
                            f"{req_i.step_number or req_i.req_key}"
                        ),
                        expected=(
                            f"SOP order: “{truncate(req_i.text, 90)}” (step {req_i.step_number or req_i.seq}) "
                            f"must precede “{truncate(req_j.text, 90)}” (step {req_j.step_number or req_j.seq})."
                        ),
                        observed=(
                            f"Report records “{truncate(match_j.event.text, 90)}” at position "
                            f"{match_j.event.seq} before “{truncate(match_i.event.text, 90)}” at position "
                            f"{match_i.event.seq}."
                        ),
                        explanation=(
                            "The execution order in the report inverts the order mandated by the SOP. "
                            "Sequence inversion was determined by comparing recorded entry positions."
                        ),
                        suggested_action=(
                            "Verify whether the inverted order affects product quality and record the rationale."
                        ),
                        requirement=req_j,
                        citations=[
                            _sop_citation(revision, req_i),
                            _sop_citation(revision, req_j),
                            _report_citation(report, match_j.event),
                            _report_citation(report, match_i.event),
                        ],
                        metrics={
                            "sop_positions": [req_i.seq, req_j.seq],
                            "report_positions": [match_i.event.seq, match_j.event.seq],
                        },
                    )
                )
                return drafts  # report the first (most significant) inversion
    return drafts


def check_duplicates(
    requirements: list[Requirement],
    events: list[ReportEvent],
    matches: dict[int, EvidenceMatch],
    revision: SopRevision,
    report: ProcessReport,
) -> list[FindingDraft]:
    drafts: list[FindingDraft] = []
    duplicates = find_duplicate_executions(requirements, events, matches)
    by_id = {r.id: r for r in requirements}
    for req_id, extra_events in duplicates.items():
        requirement = by_id[req_id]
        primary = matches[req_id].event
        assert primary is not None
        conflicting_values = []
        for event in extra_events:
            if (
                event.value_num is not None
                and primary.value_num is not None
                and abs(event.value_num - primary.value_num) > 1e-9
            ):
                conflicting_values.append(event)
        citations = [_sop_citation(revision, requirement), _report_citation(report, primary)]
        citations += [_report_citation(report, e) for e in extra_events]

        if conflicting_values:
            drafts.append(
                FindingDraft(
                    category=FindingCategory.CONTRADICTORY_STATEMENT,
                    classification=FindingClassification.CONFIRMED_DEVIATION,
                    severity=Severity.MAJOR,
                    title=f"Conflicting measurements recorded for {requirement.step_number or requirement.req_key}",
                    expected=_describe_requirement(requirement),
                    observed=(
                        "Multiple different values recorded: "
                        + ", ".join(
                            f"{e.value_num:g} {e.unit or ''}".strip()
                            for e in [primary, *conflicting_values]
                            if e.value_num is not None
                        )
                    ),
                    explanation=(
                        "The report contains more than one measurement for the same requirement with different "
                        "values. The record is internally inconsistent, so the true value cannot be established."
                    ),
                    suggested_action="Determine which measurement is valid and correct the record with justification.",
                    requirement=requirement,
                    evidence_sufficiency=EvidenceSufficiency.PARTIAL,
                    confidence=0.85,
                    citations=citations,
                    uncertainty_notes=["Contradictory values prevent a single authoritative observation."],
                    metrics={
                        "values": [e.value_num for e in [primary, *conflicting_values]],
                    },
                )
            )
        else:
            drafts.append(
                FindingDraft(
                    category=FindingCategory.DUPLICATE_STEP,
                    classification=FindingClassification.POSSIBLE_DEVIATION,
                    severity=Severity.MINOR,
                    title=f"Step {requirement.step_number or requirement.req_key} recorded more than once",
                    expected=_describe_requirement(requirement),
                    observed=f"{1 + len(extra_events)} entries match this single SOP requirement.",
                    explanation=(
                        "Multiple report entries satisfy the same requirement. This may be a duplicate record, "
                        "a legitimate repeat (e.g. rework) or a data-entry error."
                    ),
                    suggested_action="Confirm whether the step was genuinely repeated and annotate the record.",
                    requirement=requirement,
                    evidence_sufficiency=EvidenceSufficiency.PARTIAL,
                    confidence=0.6,
                    citations=citations,
                    metrics={"duplicate_count": 1 + len(extra_events)},
                )
            )
    return drafts


def check_unsupported_claims(
    report: ProcessReport,
    events: list[ReportEvent],
    deviation_drafts: list[FindingDraft],
    revision: SopRevision | None,
) -> list[FindingDraft]:
    """Flag blanket completion claims that the evidence does not support."""
    drafts: list[FindingDraft] = []
    note_events = [e for e in events if (e.raw_fields or {}).get("kind") == "operator_notes"]
    claim_sources: list[tuple[str, ReportEvent | None]] = [(e.text, e) for e in note_events]
    if report.raw_text and not note_events:
        claim_sources.append((report.raw_text, None))

    blocking = [
        d
        for d in deviation_drafts
        if d.classification
        in {FindingClassification.CONFIRMED_DEVIATION, FindingClassification.POSSIBLE_DEVIATION}
        and d.category != FindingCategory.PROMPT_INJECTION
    ]
    if not blocking:
        return drafts

    for text, event in claim_sources:
        match = COMPLETION_CLAIM_RE.search(text or "")
        if not match:
            continue
        quote = (text or "")[max(0, match.start() - 60) : match.end() + 60]
        citations: list[CitationDraft] = []
        if event is not None:
            citations.append(_report_citation(report, event))
        else:
            start = max(0, match.start())
            citations.append(
                CitationDraft(
                    source_type="report",
                    document_label=report.report_code,
                    quote=report.raw_text[start : match.end()],
                    char_start=start,
                    char_end=match.end(),
                    report_id=report.id,
                    line_ref="raw text",
                )
            )
        examples = "; ".join(truncate(d.title, 80) for d in blocking[:3])
        drafts.append(
            FindingDraft(
                category=FindingCategory.UNSUPPORTED_CLAIM,
                classification=FindingClassification.CONFIRMED_DEVIATION,
                severity=Severity.MAJOR,
                title="Completion claim not supported by the recorded evidence",
                expected="Statements in the report must be supported by the execution record.",
                observed=f"Report states: “{truncate(quote, 180)}”",
                explanation=(
                    f"The report asserts full completion/compliance, but {len(blocking)} unresolved issue(s) "
                    f"were detected from the record itself ({examples}). The claim is therefore unsupported "
                    "by the available evidence."
                ),
                suggested_action="Ask the operator to reconcile the summary statement with the step-level record.",
                evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
                confidence=0.85,
                citations=citations,
                metrics={"unsupported_against": [d.title for d in blocking[:5]]},
            )
        )
        break
    return drafts


def check_internal_contradictions(report: ProcessReport, events: list[ReportEvent]) -> list[FindingDraft]:
    """Contradictory operator statements inside one report."""
    drafts: list[FindingDraft] = []
    notes = [e for e in events if (e.raw_fields or {}).get("kind") == "operator_notes"]
    step_events = [e for e in events if (e.raw_fields or {}).get("kind") != "operator_notes"]

    for note in notes:
        note_norm = normalize_text(note.text)
        if not note_norm:
            continue
        for event in step_events:
            if not event.step_number:
                continue
            if f"step {event.step_number}" not in note_norm and event.step_number not in note_norm.split():
                continue
            note_negative = bool(NEGATION_RE.search(note.text))
            if note_negative and event.status == EventStatus.COMPLETED:
                drafts.append(
                    FindingDraft(
                        category=FindingCategory.CONTRADICTORY_STATEMENT,
                        classification=FindingClassification.CONFIRMED_DEVIATION,
                        severity=Severity.MAJOR,
                        title=f"Operator note contradicts the status recorded for step {event.step_number}",
                        expected="Step status and operator narrative must agree.",
                        observed=(
                            f"Step {event.step_number} status is '{event.status}', but the note states: "
                            f"“{truncate(note.text, 140)}”."
                        ),
                        explanation=(
                            "The structured status and the free-text note for the same step disagree, so the "
                            "actual execution state cannot be established from this record alone."
                        ),
                        suggested_action="Clarify with the operator which statement is correct and correct the record.",
                        evidence_sufficiency=EvidenceSufficiency.PARTIAL,
                        confidence=0.75,
                        citations=[_report_citation(report, event), _report_citation(report, note)],
                        uncertainty_notes=["Two conflicting statements in the same report."],
                        metrics={"step": event.step_number, "status": event.status},
                    )
                )
                break
    return drafts


def check_extra_events(
    events: list[ReportEvent],
    matches: dict[int, EvidenceMatch],
    report: ProcessReport,
    *,
    exclude_event_ids: set[int] | None = None,
) -> list[FindingDraft]:
    """Recorded activities that do not correspond to any SOP requirement."""
    extras = unmatched_events(events, matches)
    drafts: list[FindingDraft] = []
    # A repeat of a step that *is* matched elsewhere is a duplicate execution, which
    # check_duplicates already reports; re-reporting it as an unknown activity would be
    # double counting. Likewise entries consumed by the prohibited-action check.
    matched_steps = {
        (m.event.step_number or "").strip()
        for m in matches.values()
        if m.event is not None and (m.event.step_number or "").strip()
    }
    consumed = set(exclude_event_ids or ())
    for event in extras:
        if not (event.text or "").strip() or len(event.text) < 12:
            continue
        if event.id in consumed:
            continue
        if (event.step_number or "").strip() in matched_steps:
            continue
        drafts.append(
            FindingDraft(
                category=FindingCategory.AMBIGUOUS_REQUIREMENT,
                classification=FindingClassification.INSUFFICIENT_EVIDENCE,
                severity=Severity.INFO,
                title=f"Recorded activity has no matching SOP requirement (entry {event.step_number or event.seq})",
                expected="Every recorded activity should map to an approved instruction.",
                observed=truncate(event.text, 180),
                explanation=(
                    "This entry could not be matched to any requirement in the governing revision. It may be an "
                    "undocumented activity, a local practice, or a matching failure of the extraction."
                ),
                suggested_action="Confirm whether this activity is covered by another approved document.",
                evidence_sufficiency=EvidenceSufficiency.INSUFFICIENT,
                confidence=0.45,
                citations=[_report_citation(report, event)],
                uncertainty_notes=[
                    "Unmatched report entry; may indicate an extraction gap rather than a deviation."
                ],
                metrics={"event_seq": event.seq},
            )
        )
    return drafts[:6]
