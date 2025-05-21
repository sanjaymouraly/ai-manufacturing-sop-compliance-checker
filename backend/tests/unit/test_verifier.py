"""Citation verification - the control that stops the system inventing evidence.

These are the most important tests in the repository. If any of them regress, the
product's central claim ("every quote resolves to stored source text") is false.
"""

from __future__ import annotations

import pytest

from app.models.enums import (
    EvidenceSufficiency,
    FindingCategory,
    FindingClassification,
    Severity,
    VerificationStatus,
)
from app.services.audit.comparison import CitationDraft, FindingDraft
from app.services.audit.verifier import verify_citation, verify_finding

SOP_TEXT = (
    "[page 1]\n"
    "4.3 Torque the four M6 housing bolts to 12 N·m ± 1 N·m in a star pattern.\n"
    "4.4 Measure the shaft end-play; it must be between 0.05 mm and 0.15 mm.\n"
)
REPORT_TEXT = "[page 1]\n4.3 Torqued the four M6 housing bolts: 14.8 N·m recorded by T. Nguyen.\n"


class _FakeDocument:
    sop_code = "SOP-ASSY-101"


class _FakeRevision:
    """Mirrors the SopRevision attributes the verifier reads."""

    id = 1
    revision_code = "3"
    raw_text = SOP_TEXT
    document = _FakeDocument()


class _FakeReport:
    id = 7
    report_code = "PR-TEST-0001"
    raw_text = REPORT_TEXT


@pytest.fixture()
def sources() -> tuple[dict[int, object], dict[int, object]]:
    return {1: _FakeRevision()}, {7: _FakeReport()}


def _sop_citation(quote: str, start: int, end: int) -> CitationDraft:
    return CitationDraft(
        source_type="sop",
        revision_id=1,
        report_id=None,
        document_label="SOP-ASSY-101 rev 3",
        quote=quote,
        char_start=start,
        char_end=end,
        page=1,
        section_path="4.3",
    )


class TestCitationVerification:
    def test_exact_quote_at_exact_offsets_is_verified(self, sources) -> None:
        revisions, reports = sources
        quote = "12 N·m ± 1 N·m"
        start = SOP_TEXT.index(quote)
        result = verify_citation(_sop_citation(quote, start, start + len(quote)), revisions, reports)
        assert result.verified is True

    def test_fabricated_quote_is_rejected(self, sources) -> None:
        """A quote that does not exist in the source must never verify."""
        revisions, reports = sources
        fabricated = "Torque the bolts to 25 N·m as approved by the quality manager"
        result = verify_citation(_sop_citation(fabricated, 0, len(fabricated)), revisions, reports)
        assert result.verified is False
        assert "not" in (result.note or "").lower()

    def test_quote_from_a_different_document_is_rejected(self, sources) -> None:
        revisions, reports = sources
        quote = "14.8 N·m recorded by T. Nguyen"  # exists in the report, not the SOP
        result = verify_citation(_sop_citation(quote, 0, len(quote)), revisions, reports)
        assert result.verified is False

    def test_unknown_source_id_is_rejected(self, sources) -> None:
        _, reports = sources
        quote = "12 N·m ± 1 N·m"
        result = verify_citation(_sop_citation(quote, 0, len(quote)), {}, reports)
        assert result.verified is False

    def test_wrong_offsets_are_recovered_when_the_quote_is_genuine(self, sources) -> None:
        """Honest text with sloppy offsets is repaired, not silently trusted."""
        revisions, reports = sources
        quote = "0.05 mm and 0.15 mm"
        result = verify_citation(_sop_citation(quote, 9999, 10050), revisions, reports)
        assert result.verified is True
        assert SOP_TEXT[result.citation.char_start : result.citation.char_end] == quote

    def test_empty_quote_is_rejected(self, sources) -> None:
        revisions, reports = sources
        result = verify_citation(_sop_citation("", 0, 0), revisions, reports)
        assert result.verified is False


class TestFindingVerification:
    def _draft(self, citations: list[CitationDraft]) -> FindingDraft:
        return FindingDraft(
            category=FindingCategory.PARAMETER_DEVIATION,
            classification=FindingClassification.CONFIRMED_DEVIATION,
            severity=Severity.MAJOR,
            title="Torque outside tolerance",
            expected="12 N·m ± 1 N·m",
            observed="14.8 N·m",
            explanation="Recorded torque exceeds the upper limit.",
            suggested_action="Review.",
            evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
            confidence=0.9,
            citations=citations,
        )

    def test_confirmed_finding_with_fabricated_evidence_is_downgraded(self, sources) -> None:
        revisions, reports = sources
        draft = self._draft([_sop_citation("a quote that was never written", 0, 30)])
        outcome = verify_finding(draft, revisions, reports, strict=True)
        assert outcome.status == VerificationStatus.INVALID_CITATION
        assert draft.classification != FindingClassification.CONFIRMED_DEVIATION
        assert draft.classification == FindingClassification.INSUFFICIENT_EVIDENCE
        assert draft.confidence <= 0.4
        assert any(not c.verified for c in outcome.citations)

    def test_confirmed_finding_without_any_citation_is_downgraded(self, sources) -> None:
        revisions, reports = sources
        draft = self._draft([])
        outcome = verify_finding(draft, revisions, reports, strict=True)
        assert outcome.status == VerificationStatus.UNVERIFIED
        assert draft.classification != FindingClassification.CONFIRMED_DEVIATION

    def test_genuine_evidence_keeps_the_finding_intact(self, sources) -> None:
        revisions, reports = sources
        quote = "12 N·m ± 1 N·m"
        start = SOP_TEXT.index(quote)
        draft = self._draft([_sop_citation(quote, start, start + len(quote))])
        outcome = verify_finding(draft, revisions, reports, strict=True)
        assert outcome.status == VerificationStatus.VERIFIED
        assert draft.classification == FindingClassification.CONFIRMED_DEVIATION
