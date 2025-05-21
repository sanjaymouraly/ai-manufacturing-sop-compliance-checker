"""Controlled vocabularies used across the domain model.

These are plain ``str`` enums so they serialise cleanly to JSON, SQLite and the
frontend without driver-specific ENUM types.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "administrator"
    AUDITOR = "auditor"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


class RevisionStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


class Obligation(StrEnum):
    MANDATORY = "mandatory"
    CONDITIONAL = "conditional"
    OPTIONAL = "optional"
    PROHIBITED = "prohibited"
    INFORMATIVE = "informative"


class EventStatus(StrEnum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"
    UNKNOWN = "unknown"


class AuditStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AuditOutcome(StrEnum):
    """Overall, deliberately non-certifying outcome of an audit run."""

    NO_DEVIATION_DETECTED = "no_detected_deviation"
    DEVIATIONS_FOUND = "deviations_found"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    BLOCKED_UNRESOLVED_AUTHORITY = "blocked_unresolved_authority"


class FindingClassification(StrEnum):
    CONFIRMED_DEVIATION = "confirmed_deviation"
    POSSIBLE_DEVIATION = "possible_deviation"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SOP_CONFLICT = "sop_conflict"
    NO_DETECTED_DEVIATION = "no_detected_deviation"


class FindingCategory(StrEnum):
    MISSING_STEP = "missing_step"
    OUT_OF_SEQUENCE = "out_of_sequence"
    DUPLICATE_STEP = "duplicate_step"
    PARAMETER_DEVIATION = "parameter_deviation"
    UNIT_MISMATCH = "unit_mismatch"
    MISSING_RECORD = "missing_record"
    CONTRADICTORY_STATEMENT = "contradictory_statement"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    OUTDATED_SOP = "outdated_sop"
    CONFLICTING_REVISION = "conflicting_revision"
    AMBIGUOUS_REQUIREMENT = "ambiguous_requirement"
    PROMPT_INJECTION = "prompt_injection"
    EXTRACTION_QUALITY = "extraction_quality"
    PROHIBITED_ACTION = "prohibited_action"
    COMPLIANT_OBSERVATION = "compliant_observation"


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"


class EvidenceSufficiency(StrEnum):
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class Detector(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    HYBRID = "hybrid"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    INVALID_CITATION = "invalid_citation"
    NOT_APPLICABLE = "not_applicable"


class ReviewStatus(StrEnum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    INFO_REQUESTED = "info_requested"
    RESOLVED = "resolved"


class ConflictType(StrEnum):
    OVERLAPPING_EFFECTIVE_DATES = "overlapping_effective_dates"
    PARAMETER_MISMATCH = "parameter_mismatch"
    STEP_ORDER_CONFLICT = "step_order_conflict"
    MISSING_SUPERSESSION = "missing_supersession"
    UNKNOWN_APPROVAL_STATUS = "unknown_approval_status"
    WITHDRAWN_REFERENCE = "withdrawn_reference"
    CROSS_DOCUMENT_CONTRADICTION = "cross_document_contradiction"
    UNRESOLVABLE_CONTEXT = "unresolvable_context"
    REQUIREMENT_PRESENCE_MISMATCH = "requirement_presence_mismatch"


class ConflictStatus(StrEnum):
    OPEN = "open"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    ACCEPTED_RISK = "accepted_risk"


class AnalysisMode(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM_ASSISTED = "llm_assisted"


SEVERITY_ORDER: dict[str, int] = {
    Severity.CRITICAL: 0,
    Severity.MAJOR: 1,
    Severity.MINOR: 2,
    Severity.INFO: 3,
}

#: Classifications that must never be auto-closed without a human decision.
ESCALATING_CLASSIFICATIONS = {
    FindingClassification.SOP_CONFLICT,
    FindingClassification.INSUFFICIENT_EVIDENCE,
}
