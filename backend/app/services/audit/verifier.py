"""Post-processing evidence verifier.

Every citation produced by *any* detector (deterministic or LLM) is re-checked
against the stored source text before a finding can be presented as confirmed:

1. The referenced document must exist and be the one the audit actually used.
2. The character span must be inside the stored text.
3. The quoted excerpt must match the text at that span (whitespace-insensitive),
   or at minimum be locatable verbatim in the document.
4. Numeric claims in the finding must be supported by the cited text or by
   deterministic metrics.

A finding whose citations fail verification is **never** presented as a
confirmed deviation: it is downgraded, marked ``invalid_citation`` and routed to
human review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.entities import ProcessReport, SopRevision
from app.models.enums import (
    EvidenceSufficiency,
    FindingClassification,
    VerificationStatus,
)
from app.services.audit.comparison import CitationDraft, FindingDraft
from app.utils.text import normalize_whitespace, similarity

MIN_QUOTE_SIMILARITY = 0.90


@dataclass
class CitationVerification:
    citation: CitationDraft
    verified: bool
    note: str
    similarity: float


@dataclass
class VerificationOutcome:
    status: str
    notes: list[str]
    citations: list[CitationVerification]

    @property
    def all_verified(self) -> bool:
        return bool(self.citations) and all(c.verified for c in self.citations)


def _source_text(
    citation: CitationDraft,
    revisions: dict[int, SopRevision],
    reports: dict[int, ProcessReport],
) -> tuple[str | None, str]:
    if citation.source_type == "sop":
        if citation.revision_id is None:
            return None, "Citation claims an SOP source but carries no revision id."
        revision = revisions.get(citation.revision_id)
        if revision is None:
            return None, f"Referenced SOP revision id {citation.revision_id} is not part of this audit."
        return revision.raw_text, ""
    if citation.source_type == "report":
        if citation.report_id is None:
            return None, "Citation claims a report source but carries no report id."
        report = reports.get(citation.report_id)
        if report is None:
            return None, f"Referenced report id {citation.report_id} is not part of this audit."
        return report.raw_text, ""
    return None, f"Unknown citation source type '{citation.source_type}'."


def verify_citation(
    citation: CitationDraft,
    revisions: dict[int, SopRevision],
    reports: dict[int, ProcessReport],
) -> CitationVerification:
    text, error = _source_text(citation, revisions, reports)
    if text is None:
        return CitationVerification(citation, False, error, 0.0)

    quote = normalize_whitespace(citation.quote or "")
    if not quote:
        return CitationVerification(citation, False, "Citation contains no quoted text.", 0.0)

    if citation.char_start < 0 or citation.char_end > len(text) or citation.char_start >= citation.char_end:
        # offsets unusable -> try to locate the quote verbatim
        located = text.find(citation.quote)
        if located == -1:
            located_norm = normalize_whitespace(text).find(quote)
            if located_norm == -1:
                return CitationVerification(
                    citation,
                    False,
                    f"Quoted text was not found in {citation.document_label}; offsets "
                    f"[{citation.char_start}:{citation.char_end}] are also out of range.",
                    0.0,
                )
            return CitationVerification(
                citation,
                True,
                "Quote located in the source document, but the character offsets were corrected.",
                1.0,
            )
        citation.char_start, citation.char_end = located, located + len(citation.quote)
        return CitationVerification(citation, True, "Offsets recovered by locating the quote.", 1.0)

    span_text = normalize_whitespace(text[citation.char_start : citation.char_end])
    if span_text == quote:
        return CitationVerification(citation, True, "Quote matches the source span exactly.", 1.0)

    score = similarity(span_text, quote)
    if quote in span_text or span_text in quote:
        return CitationVerification(
            citation, True, "Quote is contained in the cited span.", max(score, MIN_QUOTE_SIMILARITY)
        )
    if normalize_whitespace(text).find(quote) != -1:
        return CitationVerification(
            citation,
            True,
            "Quote exists in the document but not at the cited offsets; offsets flagged as imprecise.",
            score,
        )
    return CitationVerification(
        citation,
        False,
        f"Quoted text does not appear at the cited location in {citation.document_label} "
        f"(similarity {score:.2f}).",
        score,
    )


def _numeric_claims_supported(draft: FindingDraft) -> tuple[bool, str]:
    """Numbers in an LLM narrative must come from deterministic metrics or a quote."""
    if draft.detector == "deterministic":
        return True, ""
    import re

    numbers = set(re.findall(r"-?\d+(?:\.\d+)?", f"{draft.observed} {draft.expected}"))
    if not numbers:
        return True, ""
    supported_blob = " ".join([str(v) for v in draft.metrics.values()] + [c.quote for c in draft.citations])
    unsupported = [n for n in numbers if n not in supported_blob]
    if unsupported:
        return False, (
            f"Numeric value(s) {', '.join(sorted(unsupported))} are not supported by cited evidence "
            "or deterministic metrics."
        )
    return True, ""


def verify_finding(
    draft: FindingDraft,
    revisions: dict[int, SopRevision],
    reports: dict[int, ProcessReport],
    *,
    strict: bool = True,
) -> VerificationOutcome:
    """Verify all citations of a finding and downgrade it in place if needed."""
    results = [verify_citation(c, revisions, reports) for c in draft.citations]
    notes = [r.note for r in results if r.note]

    if not results:
        status = VerificationStatus.NOT_APPLICABLE
        if draft.classification == FindingClassification.CONFIRMED_DEVIATION:
            status = VerificationStatus.UNVERIFIED
            draft.classification = FindingClassification.POSSIBLE_DEVIATION
            draft.uncertainty_notes.append(
                "Downgraded: no source citation was attached, so the claim could not be verified."
            )
        return VerificationOutcome(status=status, notes=notes, citations=results)

    supported, claim_note = _numeric_claims_supported(draft)
    if not supported:
        notes.append(claim_note)

    if all(r.verified for r in results) and supported:
        return VerificationOutcome(status=VerificationStatus.VERIFIED, notes=notes, citations=results)

    status = VerificationStatus.INVALID_CITATION
    if draft.classification == FindingClassification.CONFIRMED_DEVIATION:
        draft.classification = (
            FindingClassification.INSUFFICIENT_EVIDENCE
            if strict
            else FindingClassification.POSSIBLE_DEVIATION
        )
        draft.uncertainty_notes.append(
            "Downgraded from 'confirmed deviation': one or more citations failed verification."
        )
    elif draft.classification == FindingClassification.POSSIBLE_DEVIATION:
        draft.classification = FindingClassification.INSUFFICIENT_EVIDENCE
        draft.uncertainty_notes.append("Citation verification failed; evidence is insufficient.")
    draft.evidence_sufficiency = EvidenceSufficiency.INSUFFICIENT
    draft.confidence = min(draft.confidence, 0.4)
    draft.uncertainty_notes.extend(notes)
    return VerificationOutcome(status=status, notes=notes, citations=results)


def verification_summary(outcomes: list[VerificationOutcome]) -> dict[str, Any]:
    total = sum(len(o.citations) for o in outcomes)
    verified = sum(1 for o in outcomes for c in o.citations if c.verified)
    invalid = total - verified
    return {
        "citations_total": total,
        "citations_verified": verified,
        "citations_invalid": invalid,
        "citation_validity_rate": round(verified / total, 4) if total else 1.0,
        "findings_with_invalid_citations": sum(
            1 for o in outcomes if o.status == VerificationStatus.INVALID_CITATION
        ),
    }
