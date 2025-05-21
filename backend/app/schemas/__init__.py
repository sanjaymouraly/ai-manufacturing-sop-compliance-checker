"""Pydantic request/response models (typed API contract)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    page_size: int = 25
    pages: int = 1


# --------------------------------------------------------------------------- #
# Auth & users
# --------------------------------------------------------------------------- #
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    user: UserOut


class UserOut(ORMModel):
    id: int
    username: str
    email: str | None = None
    full_name: str | None = None
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(min_length=8, max_length=256)
    role: str = "viewer"
    email: str | None = None
    full_name: str | None = None


class UserUpdate(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    full_name: str | None = None
    email: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=256)


# --------------------------------------------------------------------------- #
# SOP documents
# --------------------------------------------------------------------------- #
class RequirementOut(ORMModel):
    id: int
    req_key: str
    seq: int
    step_number: str | None
    text: str
    obligation: str
    action_verb: str | None
    condition_text: str | None
    requires_record: bool
    parameter_name: str | None
    comparator: str | None
    value_min: float | None
    value_max: float | None
    tolerance: float | None
    unit: str | None
    section_path: str
    page: int
    char_start: int
    char_end: int
    confidence: float
    extraction_method: str
    ambiguity_notes: list[str]


class SectionOut(ORMModel):
    id: int
    section_path: str
    heading: str | None
    level: int
    kind: str
    page: int
    order_index: int
    char_start: int
    char_end: int
    text: str


class RevisionOut(ORMModel):
    id: int
    document_id: int
    revision_code: str
    title: str | None
    status: str
    effective_date: date | None
    expiry_date: date | None
    approved_by: str | None
    approval_date: date | None
    supersedes_revision_code: str | None
    superseded_by_revision_code: str | None
    applicability: dict[str, Any]
    file_name: str
    file_hash: str
    file_size: int
    source_format: str | None
    page_count: int
    extraction_confidence: float
    ocr_used: bool
    extraction_warnings: list[str]
    injection_flags: list[dict[str, Any]]
    created_at: datetime
    requirement_count: int = 0


class RevisionDetail(RevisionOut):
    raw_text: str
    sections: list[SectionOut] = []
    requirements: list[RequirementOut] = []


class DocumentOut(ORMModel):
    id: int
    sop_code: str
    title: str
    process_area: str | None
    owner: str | None
    description: str | None
    is_archived: bool
    created_at: datetime
    revisions: list[RevisionOut] = []
    # Derived, read-only summary fields. The library list needs these to be useful,
    # and computing them server-side avoids an N+1 round trip per document.
    revision_count: int = 0
    latest_revision_code: str | None = None
    approved_revision_code: str | None = None
    has_open_conflicts: bool = False


class DocumentUpdate(BaseModel):
    title: str | None = None
    process_area: str | None = None
    owner: str | None = None
    description: str | None = None
    is_archived: bool | None = None


class RevisionStatusUpdate(BaseModel):
    status: str
    reason: str = Field(min_length=5, max_length=1000)


class DiffOut(BaseModel):
    left_revision_id: int
    right_revision_id: int
    left_label: str
    right_label: str
    summary: dict[str, int]
    deltas: list[dict[str, Any]]


# --------------------------------------------------------------------------- #
# Process reports
# --------------------------------------------------------------------------- #
class ReportEventOut(ORMModel):
    id: int
    seq: int
    step_number: str | None
    text: str
    status: str
    operator: str | None
    timestamp_text: str | None
    parameter_name: str | None
    value_num: float | None
    value_text: str | None
    unit: str | None
    page: int
    line_ref: str | None
    char_start: int
    char_end: int


class ProcessReportOut(ORMModel):
    id: int
    report_code: str
    title: str | None
    batch_id: str | None
    line_id: str | None
    product_code: str | None
    site: str | None
    operator: str | None
    executed_at: date | None
    declared_sop_code: str | None
    declared_revision_code: str | None
    file_name: str | None
    file_hash: str
    source_format: str | None
    extraction_confidence: float
    ocr_used: bool
    extraction_warnings: list[str]
    injection_flags: list[dict[str, Any]]
    created_at: datetime
    event_count: int = 0
    audit_count: int = 0


class ProcessReportDetail(ProcessReportOut):
    raw_text: str
    events: list[ReportEventOut] = []


class ManualEventIn(BaseModel):
    step: str | None = None
    description: str = Field(min_length=1, max_length=2000)
    status: str = "completed"
    timestamp: str | None = None
    operator: str | None = None
    parameter: str | None = None
    value: float | str | None = None
    unit: str | None = None


class ManualReportIn(BaseModel):
    report_code: str | None = None
    title: str | None = None
    batch_id: str | None = None
    line_id: str | None = None
    product_code: str | None = None
    site: str | None = None
    operator: str | None = None
    executed_at: str | None = None
    sop_code: str = Field(min_length=1, max_length=64)
    sop_revision: str | None = None
    events: list[ManualEventIn] = Field(default_factory=list)
    operator_notes: str | None = None


# --------------------------------------------------------------------------- #
# Audits & findings
# --------------------------------------------------------------------------- #
class CitationOut(ORMModel):
    id: int
    source_type: str
    revision_id: int | None
    report_id: int | None
    document_label: str
    section_path: str | None
    page: int | None
    line_ref: str | None
    char_start: int
    char_end: int
    quote: str
    verified: bool
    verification_note: str | None
    similarity: float


class FindingOut(ORMModel):
    id: int
    audit_id: int
    finding_code: str
    classification: str
    category: str
    severity: str
    requirement_id: int | None
    requirement_key: str | None
    sop_code: str | None
    revision_code: str | None
    title: str
    expected: str
    observed: str
    explanation: str
    suggested_action: str
    evidence_sufficiency: str
    confidence: float
    detector: str
    verification_status: str
    verification_notes: list[str]
    uncertainty_notes: list[str]
    metrics: dict[str, Any]
    review_status: str
    reviewer_id: int | None
    reviewed_at: datetime | None
    reviewer_comment: str | None
    conflict_id: int | None
    original_payload: dict[str, Any]
    created_at: datetime
    citations: list[CitationOut] = []


class AuditOut(ORMModel):
    id: int
    audit_code: str
    report_id: int
    resolved_revision_id: int | None
    status: str
    outcome: str | None
    mode: str
    provider_used: str | None
    model_used: str | None
    requires_human_review: bool
    review_status: str
    assigned_reviewer_id: int | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int
    cost_usd: float
    token_usage: dict[str, Any]
    summary: dict[str, Any]
    engine_version: str
    created_at: datetime
    report_code: str | None = None
    sop_code: str | None = None
    revision_code: str | None = None
    finding_count: int = 0


class AuditDetail(AuditOut):
    version_resolution: dict[str, Any]
    pipeline_log: list[dict[str, Any]]
    input_snapshot: dict[str, Any]
    error_message: str | None = None
    findings: list[FindingOut] = []


class AuditCreate(BaseModel):
    report_id: int
    mode: str | None = None

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str | None) -> str | None:
        if v is not None and v not in {"deterministic", "llm_assisted"}:
            raise ValueError("mode must be 'deterministic' or 'llm_assisted'")
        return v


class ReviewActionIn(BaseModel):
    action: str = Field(pattern=r"^(accept|reject|request_info|assign|resolve|reopen)$")
    reason: str = Field(min_length=10, max_length=4000)
    assignee_id: int | None = None


class ReviewActionOut(ORMModel):
    id: int
    entity_type: str
    entity_id: int
    action: str
    from_state: str | None
    to_state: str | None
    reason: str
    payload: dict[str, Any]
    actor_id: int | None
    actor_name: str | None
    created_at: datetime


# --------------------------------------------------------------------------- #
# Conflicts
# --------------------------------------------------------------------------- #
class ConflictOut(ORMModel):
    id: int
    conflict_code: str
    conflict_type: str
    severity: str
    status: str
    document_id: int | None
    revision_a_id: int | None
    revision_b_id: int | None
    title: str
    description: str
    evidence_a: dict[str, Any]
    evidence_b: dict[str, Any]
    applicable_context: dict[str, Any]
    blocks_conclusion: bool
    resolution_rationale: str | None
    resolution_choice: str | None
    resolved_by_id: int | None
    resolved_at: datetime | None
    created_at: datetime
    sop_code: str | None = None
    # Resolved for display: the Conflict Center must never show bare row ids to a
    # non-technical reviewer.
    revision_a_code: str | None = None
    revision_b_code: str | None = None
    resolved_by: str | None = None


class ConflictResolveIn(BaseModel):
    choice: str = Field(min_length=1, max_length=64)
    rationale: str = Field(min_length=10, max_length=4000)
    accept_risk: bool = False


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
class ProviderConfigIn(BaseModel):
    name: str = "primary"
    provider_type: str
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    timeout_seconds: int = Field(default=60, ge=5, le=600)
    max_tokens: int = Field(default=1200, ge=64, le=32000)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    allow_paid_requests: bool = False
    request_budget_usd: float = Field(default=1.0, ge=0.0, le=1000.0)
    # Works where request_budget_usd cannot: a provider that publishes no
    # prices yields a $0 estimate, which no dollar cap can ever exceed.
    token_budget: int = Field(default=0, ge=0, le=100_000_000)
    make_active: bool = True


class ProviderConfigOut(ORMModel):
    id: int
    name: str
    provider_type: str
    base_url: str | None
    model: str | None
    api_key_last4: str | None
    timeout_seconds: int
    max_tokens: int
    temperature: float
    is_active: bool
    allow_paid_requests: bool
    request_budget_usd: float
    spent_usd: float
    token_budget: int
    spent_tokens: int
    last_test_at: datetime | None
    last_test_ok: bool | None
    last_test_message: str | None
    capabilities: dict[str, Any]
    created_at: datetime


class ProviderTestIn(BaseModel):
    config_id: int | None = None
    provider_type: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    run_inference: bool = False
    """Live inference costs money; must be explicitly requested AND allowed by the config."""


# --------------------------------------------------------------------------- #
# Misc
# --------------------------------------------------------------------------- #
class HealthOut(BaseModel):
    status: str
    version: str
    environment: str
    database: str
    analysis_mode: str
    llm_provider: str
    llm_configured: bool
    documents: int
    audits: int
    open_conflicts: int
    uptime_seconds: float


class MessageOut(BaseModel):
    message: str
    detail: Any | None = None


TokenResponse.model_rebuild()
