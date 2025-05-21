"""Risk classification and overall audit outcome.

Deliberately conservative: the "no detected deviation" outcome is only reached
when evidence was sufficient everywhere, and it is always expressed as
*no deviation detected* — never as certification, approval or release.
"""

from __future__ import annotations

from typing import Any

from app.models.enums import (
    SEVERITY_ORDER,
    AuditOutcome,
    Detector,
    EvidenceSufficiency,
    FindingCategory,
    FindingClassification,
    Severity,
)
from app.services.audit.comparison import FindingDraft

DISCLAIMER = (
    "AI-assisted assessment for decision support only. This output does not certify the process, "
    "authorise product release, or establish regulatory compliance. All findings require verification "
    "by a qualified manufacturing quality reviewer."
)


def escalation_required(drafts: list[FindingDraft], *, authority_blocked: bool) -> tuple[bool, list[str]]:
    """Decide whether the audit must be routed to a human reviewer.

    An AI-assisted observation can trip these rules, because a language model
    assigns its own severity. That is deliberately left intact — erring toward
    a human read is the safe direction — but the reason is labelled so a
    reviewer can tell at a glance whether the queue entry rests on a verified
    deterministic finding or on an unconfirmed model suggestion. The two
    deserve very different amounts of attention, and a reason line that hides
    the difference quietly inflates the apparent severity of the backlog.
    """
    reasons: list[str] = []
    if authority_blocked:
        reasons.append("The governing SOP revision could not be determined unambiguously.")
    for draft in drafts:
        origin = " [AI-assisted observation]" if draft.detector == Detector.LLM else ""
        if draft.classification == FindingClassification.SOP_CONFLICT:
            reasons.append(f"Unresolved SOP conflict: {draft.title}{origin}")
        elif draft.classification == FindingClassification.INSUFFICIENT_EVIDENCE and draft.severity in {
            Severity.CRITICAL,
            Severity.MAJOR,
        }:
            reasons.append(f"Insufficient evidence on a material item: {draft.title}{origin}")
        elif draft.classification == FindingClassification.CONFIRMED_DEVIATION and draft.severity in {
            Severity.CRITICAL,
            Severity.MAJOR,
        }:
            reasons.append(f"Material deviation requires review: {draft.title}{origin}")
        elif draft.classification == FindingClassification.POSSIBLE_DEVIATION and draft.severity in {
            Severity.CRITICAL,
            Severity.MAJOR,
        }:
            # Material but uncertain is exactly the case a human reviewer exists for.
            reasons.append(f"Possible material deviation requires review: {draft.title}{origin}")
        elif draft.category == FindingCategory.PROMPT_INJECTION:
            reasons.append("Document contains embedded instructions targeting the analysis system.")
    # de-duplicate, keeping first-seen order
    unique = list(dict.fromkeys(reasons))
    return bool(unique), unique


def compute_outcome(drafts: list[FindingDraft], *, authority_blocked: bool) -> str:
    if authority_blocked:
        return AuditOutcome.BLOCKED_UNRESOLVED_AUTHORITY
    has_conflict = any(d.classification == FindingClassification.SOP_CONFLICT for d in drafts)
    insufficient = [
        d
        for d in drafts
        if d.classification == FindingClassification.INSUFFICIENT_EVIDENCE
        and d.severity in {Severity.CRITICAL, Severity.MAJOR}
    ]
    deviations = [
        d
        for d in drafts
        if d.classification
        in {FindingClassification.CONFIRMED_DEVIATION, FindingClassification.POSSIBLE_DEVIATION}
    ]
    if has_conflict:
        return AuditOutcome.HUMAN_REVIEW_REQUIRED
    if deviations:
        return AuditOutcome.DEVIATIONS_FOUND
    if insufficient:
        return AuditOutcome.HUMAN_REVIEW_REQUIRED
    return AuditOutcome.NO_DEVIATION_DETECTED


def summarise(drafts: list[FindingDraft], *, authority_blocked: bool) -> dict[str, Any]:
    by_classification: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for draft in drafts:
        by_classification[draft.classification] = by_classification.get(draft.classification, 0) + 1
        by_severity[draft.severity] = by_severity.get(draft.severity, 0) + 1
        by_category[draft.category] = by_category.get(draft.category, 0) + 1

    highest: Severity = Severity.INFO
    for draft in drafts:
        if draft.classification == FindingClassification.NO_DETECTED_DEVIATION:
            continue
        if SEVERITY_ORDER[draft.severity] < SEVERITY_ORDER[highest]:
            highest = Severity(draft.severity)

    insufficient = sum(1 for d in drafts if d.evidence_sufficiency != EvidenceSufficiency.SUFFICIENT)
    escalate, reasons = escalation_required(drafts, authority_blocked=authority_blocked)
    return {
        "total_findings": len(drafts),
        "by_classification": by_classification,
        "by_severity": by_severity,
        "by_category": by_category,
        "highest_severity": highest,
        "items_with_limited_evidence": insufficient,
        "requires_human_review": escalate,
        "escalation_reasons": reasons,
        "outcome": compute_outcome(drafts, authority_blocked=authority_blocked),
        "disclaimer": DISCLAIMER,
    }


OUTCOME_LABELS = {
    AuditOutcome.NO_DEVIATION_DETECTED: "No deviation detected (not a certification)",
    AuditOutcome.DEVIATIONS_FOUND: "Deviations detected – requires verification",
    AuditOutcome.HUMAN_REVIEW_REQUIRED: "Human review required",
    AuditOutcome.BLOCKED_UNRESOLVED_AUTHORITY: "Blocked – governing SOP revision unresolved",
}
