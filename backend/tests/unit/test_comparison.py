"""Deterministic comparison rules: severity grading and conditional/optional handling."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.enums import Obligation, Severity
from app.services.audit.comparison import (
    _acceptance_band,
    _severity_for_deviation,
    evaluate_condition,
)
from app.utils.units import compare


def _requirement(**kwargs) -> SimpleNamespace:
    base = {
        "id": 1,
        "step_number": "4.3",
        "text": "",
        "normalized_text": "",
        "obligation": Obligation.MANDATORY,
        "action_verb": None,
        "condition_text": None,
        "requires_record": False,
        "record_field": None,
        "parameter_name": None,
        "comparator": None,
        "value_min": None,
        "value_max": None,
        "nominal": None,
        "tolerance": None,
        "unit": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _event(text: str, step: str = "4.2") -> SimpleNamespace:
    return SimpleNamespace(id=abs(hash(text)) % 9999, step_number=step, text=text, status="completed")


class TestSeverityGrading:
    """Severity must reflect the tolerance band, not the distance from nominal."""

    def test_tight_tolerance_overshoot_is_not_minor(self) -> None:
        requirement = _requirement(comparator="==", value_min=25.0, tolerance=0.3, unit="mm")
        result = compare(25.5, "mm", "==", 25.0, None, "mm", tolerance=0.3)
        # 2 % from nominal, but two thirds of the band beyond the limit.
        assert _severity_for_deviation(result, requirement) == Severity.MAJOR

    def test_overshoot_beyond_the_whole_half_band_is_critical(self) -> None:
        requirement = _requirement(comparator="between", value_min=11.0, value_max=13.0, unit="N·m")
        result = compare(14.8, "N·m", "between", 11.0, 13.0, "N·m")
        assert _severity_for_deviation(result, requirement) == Severity.CRITICAL

    def test_marginal_overshoot_is_minor(self) -> None:
        requirement = _requirement(comparator="between", value_min=11.0, value_max=13.0, unit="N·m")
        result = compare(13.1, "N·m", "between", 11.0, 13.0, "N·m")
        assert _severity_for_deviation(result, requirement) == Severity.MINOR

    def test_open_ended_limits_fall_back_to_percentage(self) -> None:
        requirement = _requirement(comparator=">=", value_min=60.0, unit="%")
        result = compare(45.0, "%", ">=", 60.0, None, "%")
        assert _severity_for_deviation(result, requirement) == Severity.CRITICAL

    def test_acceptance_band_from_nominal_and_tolerance(self) -> None:
        requirement = _requirement(comparator="==", value_min=25.0, tolerance=0.3)
        assert _acceptance_band(requirement) == (24.7, 25.3)

    def test_open_ended_requirement_has_no_band(self) -> None:
        assert _acceptance_band(_requirement(comparator=">=", value_min=60.0)) is None


class TestConditionalTriggers:
    """A conditional step is only expected when its trigger actually occurred."""

    @pytest.fixture()
    def report(self) -> SimpleNamespace:
        return SimpleNamespace(raw_text="", source_format="json")

    def test_negative_observation_means_not_triggered(self, report) -> None:
        requirement = _requirement(obligation=Obligation.CONDITIONAL, condition_text="If any defect is found")
        events = [_event("Inspected die surface for cracks and recorded result: no cracks found")]
        state, _ = evaluate_condition(requirement, report, events)
        assert state == "not_triggered"

    def test_positive_observation_means_triggered(self, report) -> None:
        requirement = _requirement(obligation=Obligation.CONDITIONAL, condition_text="If any defect is found")
        events = [_event("Inspected die surface: crack found on die edge")]
        state, evidence = evaluate_condition(requirement, report, events)
        assert state == "triggered"
        assert "crack found" in evidence

    def test_purpose_then_result_reads_the_result(self, report) -> None:
        """'Inspected for damage - no damage found' is a negative observation."""
        requirement = _requirement(
            obligation=Obligation.CONDITIONAL, condition_text="If the packaging is damaged"
        )
        events = [_event("Inspected packaging for transport damage - no damage found")]
        assert evaluate_condition(requirement, report, events)[0] == "not_triggered"

    def test_negating_prefix_is_understood(self, report) -> None:
        requirement = _requirement(
            obligation=Obligation.CONDITIONAL, condition_text="If the packaging is damaged"
        )
        events = [_event("Packaging undamaged, photograph and quarantine not required")]
        assert evaluate_condition(requirement, report, events)[0] == "not_triggered"

    def test_damage_found_triggers(self, report) -> None:
        requirement = _requirement(
            obligation=Obligation.CONDITIONAL, condition_text="If the packaging is damaged"
        )
        events = [_event("Inspected packaging - transport damage found on two cartons")]
        assert evaluate_condition(requirement, report, events)[0] == "triggered"

    def test_vague_condition_is_unknown_not_guessed(self, report) -> None:
        requirement = _requirement(obligation=Obligation.CONDITIONAL, condition_text="Where applicable")
        assert evaluate_condition(requirement, report, [_event("Did something")])[0] == "unknown"

    def test_subject_mention_alone_proves_nothing(self, report) -> None:
        requirement = _requirement(
            obligation=Obligation.CONDITIONAL, condition_text="If the packaging is damaged"
        )
        events = [_event("Packaging moved to the staging area")]
        assert evaluate_condition(requirement, report, events)[0] == "unknown"

    def test_no_relevant_record_is_unknown(self, report) -> None:
        requirement = _requirement(obligation=Obligation.CONDITIONAL, condition_text="If any defect is found")
        events = [_event("Recorded ambient temperature 25 C")]
        assert evaluate_condition(requirement, report, events)[0] == "unknown"


def test_escalation_reason_identifies_an_ai_originated_trigger() -> None:
    """A model-suggested item and a verified deviation must not read alike.

    An LLM assigns its own severity, so a single unconfirmed observation at
    "major" can route an otherwise clean audit to a reviewer. Escalating is the
    safe choice and is left in place, but the queue entry has to say that the
    trigger was an AI suggestion rather than a confirmed finding.
    """
    from app.models.enums import Detector, FindingClassification, Severity
    from app.services.audit.comparison import FindingDraft
    from app.services.audit.risk import escalation_required

    def draft(title: str, detector: str) -> FindingDraft:
        return FindingDraft(
            category="missing_step",
            classification=FindingClassification.INSUFFICIENT_EVIDENCE,
            severity=Severity.MAJOR,
            title=title,
            expected="A record of the step.",
            observed="No record present.",
            explanation="",
            suggested_action="Review the record.",
            detector=detector,
        )

    ai_draft = draft("Step 4.8 has no corresponding record", Detector.LLM)
    deterministic_draft = draft("Mandatory step 4.4 not recorded", Detector.DETERMINISTIC)

    escalate, reasons = escalation_required([ai_draft], authority_blocked=False)
    assert escalate, "an unconfirmed but material observation should still reach a human"
    assert any("[AI-assisted observation]" in r for r in reasons)

    escalate, reasons = escalation_required([deterministic_draft], authority_blocked=False)
    assert escalate
    assert not any("[AI-assisted observation]" in r for r in reasons), (
        "a deterministic finding must not be labelled as AI-assisted"
    )
