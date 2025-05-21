"""ORM entities.

Design notes
------------
* Every artefact that can appear in a report keeps *stable identifiers* and
  *character offsets* into the stored raw text so citations can be re-verified
  at any time against the original extraction.
* ``ReviewAction`` and ``ApiAuditEvent`` are append-only: the application never
  updates or deletes rows there, which gives a tamper-evident history.
* Original AI/deterministic findings are preserved verbatim; reviewer outcomes
  are stored alongside (``review_status`` + ``ReviewAction`` rows) rather than
  overwriting the machine result.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import (
    AnalysisMode,
    AuditStatus,
    ConflictStatus,
    Detector,
    EventStatus,
    EvidenceSufficiency,
    FindingClassification,
    Obligation,
    ReviewStatus,
    RevisionStatus,
    Role,
    Severity,
    VerificationStatus,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), default=None)
    full_name: Mapped[str | None] = mapped_column(String(160), default=None)
    role: Mapped[str] = mapped_column(String(32), default=Role.VIEWER, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


# --------------------------------------------------------------------------- #
# SOP documents
# --------------------------------------------------------------------------- #
class SopDocument(Base, TimestampMixin):
    """A logical SOP (``sop_code``) that owns one or more revisions."""

    __tablename__ = "sop_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    sop_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    process_area: Mapped[str | None] = mapped_column(String(128), default=None, index=True)
    owner: Mapped[str | None] = mapped_column(String(128), default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    revisions: Mapped[list[SopRevision]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="SopRevision.id"
    )


class SopRevision(Base, TimestampMixin):
    """A concrete uploaded revision of an SOP, with lifecycle metadata."""

    __tablename__ = "sop_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "revision_code", name="uq_revision_per_document"),
        Index("ix_revision_status_effective", "status", "effective_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("sop_documents.id", ondelete="CASCADE"), index=True)
    revision_code: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str | None] = mapped_column(String(255), default=None)
    status: Mapped[str] = mapped_column(String(24), default=RevisionStatus.UNKNOWN, index=True)
    effective_date: Mapped[date | None] = mapped_column(Date, default=None, index=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, default=None)
    approved_by: Mapped[str | None] = mapped_column(String(128), default=None)
    approval_date: Mapped[date | None] = mapped_column(Date, default=None)
    supersedes_revision_code: Mapped[str | None] = mapped_column(String(32), default=None)
    superseded_by_revision_code: Mapped[str | None] = mapped_column(String(32), default=None)

    # Applicability context, e.g. {"lines": ["L1"], "products": ["WID-100"], "sites": ["Plant A"]}
    applicability: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # File / extraction metadata
    file_name: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[str | None] = mapped_column(String(128), default=None)
    storage_path: Mapped[str | None] = mapped_column(String(512), default=None)
    source_format: Mapped[str | None] = mapped_column(String(16), default=None)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=1.0)
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False)
    extraction_warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    injection_flags: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    document: Mapped[SopDocument] = relationship(back_populates="revisions")
    sections: Mapped[list[DocumentSection]] = relationship(
        back_populates="revision", cascade="all, delete-orphan", order_by="DocumentSection.order_index"
    )
    requirements: Mapped[list[Requirement]] = relationship(
        back_populates="revision", cascade="all, delete-orphan", order_by="Requirement.seq"
    )

    @property
    def label(self) -> str:
        return f"{self.document.sop_code} {self.revision_code}" if self.document else self.revision_code


class DocumentSection(Base):
    """Structural node of an SOP revision (heading, paragraph, step, table row)."""

    __tablename__ = "document_sections"
    __table_args__ = (Index("ix_section_rev_order", "revision_id", "order_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("sop_revisions.id", ondelete="CASCADE"), index=True)
    section_path: Mapped[str] = mapped_column(String(64), default="")
    heading: Mapped[str | None] = mapped_column(String(255), default=None)
    level: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(24), default="paragraph")
    page: Mapped[int] = mapped_column(Integer, default=1)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")

    revision: Mapped[SopRevision] = relationship(back_populates="sections")


class Requirement(Base):
    """A normalised, individually addressable SOP requirement."""

    __tablename__ = "requirements"
    __table_args__ = (
        UniqueConstraint("revision_id", "req_key", name="uq_requirement_key"),
        Index("ix_requirement_param", "revision_id", "parameter_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("sop_revisions.id", ondelete="CASCADE"), index=True)
    section_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_sections.id", ondelete="SET NULL"), default=None
    )
    req_key: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    step_number: Mapped[str | None] = mapped_column(String(24), default=None, index=True)
    text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text, default="")
    obligation: Mapped[str] = mapped_column(String(16), default=Obligation.MANDATORY, index=True)
    action_verb: Mapped[str | None] = mapped_column(String(48), default=None)
    condition_text: Mapped[str | None] = mapped_column(Text, default=None)
    requires_record: Mapped[bool] = mapped_column(Boolean, default=False)
    record_field: Mapped[str | None] = mapped_column(String(96), default=None)

    # Parameter constraint (deterministically evaluated when present)
    parameter_name: Mapped[str | None] = mapped_column(String(96), default=None)
    comparator: Mapped[str | None] = mapped_column(String(16), default=None)  # ==,>=,<=,between,!=
    value_min: Mapped[float | None] = mapped_column(Float, default=None)
    value_max: Mapped[float | None] = mapped_column(Float, default=None)
    nominal: Mapped[float | None] = mapped_column(Float, default=None)
    tolerance: Mapped[float | None] = mapped_column(Float, default=None)
    unit: Mapped[str | None] = mapped_column(String(24), default=None)

    # Provenance
    section_path: Mapped[str] = mapped_column(String(64), default="")
    page: Mapped[int] = mapped_column(Integer, default=1)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    extraction_method: Mapped[str] = mapped_column(String(16), default="rule")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    ambiguity_notes: Mapped[list[str]] = mapped_column(JSON, default=list)

    revision: Mapped[SopRevision] = relationship(back_populates="requirements")


# --------------------------------------------------------------------------- #
# Process execution reports
# --------------------------------------------------------------------------- #
class ProcessReport(Base, TimestampMixin):
    __tablename__ = "process_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str | None] = mapped_column(String(255), default=None)
    batch_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    line_id: Mapped[str | None] = mapped_column(String(64), default=None)
    product_code: Mapped[str | None] = mapped_column(String(64), default=None)
    site: Mapped[str | None] = mapped_column(String(64), default=None)
    operator: Mapped[str | None] = mapped_column(String(96), default=None)
    executed_at: Mapped[date | None] = mapped_column(Date, default=None, index=True)
    declared_sop_code: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    declared_revision_code: Mapped[str | None] = mapped_column(String(32), default=None)

    file_name: Mapped[str | None] = mapped_column(String(255), default=None)
    file_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str | None] = mapped_column(String(512), default=None)
    source_format: Mapped[str | None] = mapped_column(String(16), default=None)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    extraction_confidence: Mapped[float] = mapped_column(Float, default=1.0)
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False)
    extraction_warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    injection_flags: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)

    events: Mapped[list[ReportEvent]] = relationship(
        back_populates="report", cascade="all, delete-orphan", order_by="ReportEvent.seq"
    )
    audits: Mapped[list[Audit]] = relationship(back_populates="report", cascade="all, delete-orphan")


class ReportEvent(Base):
    """A single observed execution event extracted from a process report."""

    __tablename__ = "report_events"
    __table_args__ = (Index("ix_event_report_seq", "report_id", "seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("process_reports.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    step_number: Mapped[str | None] = mapped_column(String(24), default=None, index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=EventStatus.UNKNOWN, index=True)
    operator: Mapped[str | None] = mapped_column(String(96), default=None)
    timestamp_text: Mapped[str | None] = mapped_column(String(64), default=None)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    parameter_name: Mapped[str | None] = mapped_column(String(96), default=None)
    value_num: Mapped[float | None] = mapped_column(Float, default=None)
    value_text: Mapped[str | None] = mapped_column(String(255), default=None)
    unit: Mapped[str | None] = mapped_column(String(24), default=None)

    page: Mapped[int] = mapped_column(Integer, default=1)
    line_ref: Mapped[str | None] = mapped_column(String(32), default=None)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    raw_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    report: Mapped[ProcessReport] = relationship(back_populates="events")


# --------------------------------------------------------------------------- #
# Audits & findings
# --------------------------------------------------------------------------- #
class Audit(Base, TimestampMixin):
    __tablename__ = "audits"
    __table_args__ = (Index("ix_audit_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    audit_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("process_reports.id", ondelete="CASCADE"), index=True)
    resolved_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("sop_revisions.id", ondelete="SET NULL"), default=None, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default=AuditStatus.QUEUED, index=True)
    outcome: Mapped[str | None] = mapped_column(String(40), default=None, index=True)
    mode: Mapped[str] = mapped_column(String(20), default=AnalysisMode.DETERMINISTIC)
    provider_used: Mapped[str | None] = mapped_column(String(32), default=None)
    model_used: Mapped[str | None] = mapped_column(String(96), default=None)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    review_status: Mapped[str] = mapped_column(String(20), default=ReviewStatus.OPEN, index=True)
    assigned_reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None, index=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    token_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    version_resolution: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    pipeline_log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    engine_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)

    report: Mapped[ProcessReport] = relationship(back_populates="audits")
    resolved_revision: Mapped[SopRevision | None] = relationship()
    findings: Mapped[list[AuditFinding]] = relationship(
        back_populates="audit", cascade="all, delete-orphan", order_by="AuditFinding.id"
    )


class AuditFinding(Base, TimestampMixin):
    __tablename__ = "audit_findings"
    __table_args__ = (
        Index("ix_finding_audit_cls", "audit_id", "classification"),
        Index("ix_finding_review", "review_status", "severity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    audit_id: Mapped[int] = mapped_column(ForeignKey("audits.id", ondelete="CASCADE"), index=True)
    finding_code: Mapped[str] = mapped_column(String(48), index=True)
    classification: Mapped[str] = mapped_column(String(32), default=FindingClassification.POSSIBLE_DEVIATION)
    category: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(16), default=Severity.MINOR, index=True)

    requirement_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="SET NULL"), default=None
    )
    requirement_key: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    sop_code: Mapped[str | None] = mapped_column(String(64), default=None)
    revision_code: Mapped[str | None] = mapped_column(String(32), default=None)

    title: Mapped[str] = mapped_column(String(255), default="")
    expected: Mapped[str] = mapped_column(Text, default="")
    observed: Mapped[str] = mapped_column(Text, default="")
    explanation: Mapped[str] = mapped_column(Text, default="")
    suggested_action: Mapped[str] = mapped_column(Text, default="")

    evidence_sufficiency: Mapped[str] = mapped_column(String(16), default=EvidenceSufficiency.SUFFICIENT)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    detector: Mapped[str] = mapped_column(String(16), default=Detector.DETERMINISTIC, index=True)
    verification_status: Mapped[str] = mapped_column(
        String(24), default=VerificationStatus.UNVERIFIED, index=True
    )
    verification_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    uncertainty_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Original machine output is frozen here, even if reviewers change the status.
    original_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    review_status: Mapped[str] = mapped_column(String(20), default=ReviewStatus.OPEN, index=True)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    reviewer_comment: Mapped[str | None] = mapped_column(Text, default=None)
    conflict_id: Mapped[int | None] = mapped_column(
        ForeignKey("sop_conflicts.id", ondelete="SET NULL"), default=None
    )

    audit: Mapped[Audit] = relationship(back_populates="findings")
    citations: Mapped[list[Citation]] = relationship(
        back_populates="finding", cascade="all, delete-orphan", order_by="Citation.id"
    )


class Citation(Base):
    """A verifiable pointer into a stored source document."""

    __tablename__ = "citations"

    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("audit_findings.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(16))  # sop | report
    revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("sop_revisions.id", ondelete="SET NULL"), default=None
    )
    report_id: Mapped[int | None] = mapped_column(
        ForeignKey("process_reports.id", ondelete="SET NULL"), default=None
    )
    document_label: Mapped[str] = mapped_column(String(128), default="")
    section_path: Mapped[str | None] = mapped_column(String(64), default=None)
    page: Mapped[int | None] = mapped_column(Integer, default=None)
    line_ref: Mapped[str | None] = mapped_column(String(32), default=None)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    quote: Mapped[str] = mapped_column(Text, default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    verification_note: Mapped[str | None] = mapped_column(String(255), default=None)
    similarity: Mapped[float] = mapped_column(Float, default=0.0)

    finding: Mapped[AuditFinding] = relationship(back_populates="citations")


# --------------------------------------------------------------------------- #
# Conflicts, review trail, provider config, exports
# --------------------------------------------------------------------------- #
class SopConflict(Base, TimestampMixin):
    __tablename__ = "sop_conflicts"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_conflict_fingerprint"),
        Index("ix_conflict_status_sev", "status", "severity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conflict_code: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    conflict_type: Mapped[str] = mapped_column(String(48), index=True)
    severity: Mapped[str] = mapped_column(String(16), default=Severity.MAJOR, index=True)
    status: Mapped[str] = mapped_column(String(20), default=ConflictStatus.OPEN, index=True)

    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("sop_documents.id", ondelete="CASCADE"), default=None
    )
    revision_a_id: Mapped[int | None] = mapped_column(
        ForeignKey("sop_revisions.id", ondelete="CASCADE"), default=None
    )
    revision_b_id: Mapped[int | None] = mapped_column(
        ForeignKey("sop_revisions.id", ondelete="CASCADE"), default=None
    )
    requirement_a_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="SET NULL"), default=None
    )
    requirement_b_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="SET NULL"), default=None
    )

    title: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    evidence_a: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_b: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    applicable_context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    blocks_conclusion: Mapped[bool] = mapped_column(Boolean, default=True)

    resolution_rationale: Mapped[str | None] = mapped_column(Text, default=None)
    resolution_choice: Mapped[str | None] = mapped_column(String(64), default=None)
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    document: Mapped[SopDocument | None] = relationship()
    revision_a: Mapped[SopRevision | None] = relationship(foreign_keys=[revision_a_id])
    revision_b: Mapped[SopRevision | None] = relationship(foreign_keys=[revision_b_id])


class ReviewAction(Base):
    """Append-only reviewer decision log."""

    __tablename__ = "review_actions"
    __table_args__ = (Index("ix_review_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32))  # finding | audit | conflict | document
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    from_state: Mapped[str | None] = mapped_column(String(40), default=None)
    to_state: Mapped[str | None] = mapped_column(String(40), default=None)
    reason: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None, index=True)
    actor_name: Mapped[str | None] = mapped_column(String(96), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ApiAuditEvent(Base):
    """Append-only technical event log (uploads, audits, exports, logins)."""

    __tablename__ = "api_audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    actor_name: Mapped[str | None] = mapped_column(String(96), default=None)
    entity_type: Mapped[str | None] = mapped_column(String(32), default=None)
    entity_id: Mapped[int | None] = mapped_column(Integer, default=None)
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ProviderConfig(Base, TimestampMixin):
    """Backend-managed LLM provider configuration (API key never leaves the API)."""

    __tablename__ = "provider_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    provider_type: Mapped[str] = mapped_column(String(24))
    base_url: Mapped[str | None] = mapped_column(String(255), default=None)
    model: Mapped[str | None] = mapped_column(String(128), default=None)
    api_key_encrypted: Mapped[str | None] = mapped_column(String(512), default=None)
    api_key_last4: Mapped[str | None] = mapped_column(String(8), default=None)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    max_tokens: Mapped[int] = mapped_column(Integer, default=1200)
    temperature: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    allow_paid_requests: Mapped[bool] = mapped_column(Boolean, default=False)
    request_budget_usd: Mapped[float] = mapped_column(Float, default=1.0)
    spent_usd: Mapped[float] = mapped_column(Float, default=0.0)
    # Enforceable even when the provider publishes no prices. 0 = no ceiling.
    # server_default mirrors the migration so existing rows backfill cleanly
    # and the ORM/migration drift check stays green.
    token_budget: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    spent_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, default=None)
    last_test_message: Mapped[str | None] = mapped_column(Text, default=None)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ExportArtifact(Base, TimestampMixin):
    __tablename__ = "export_artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    audit_id: Mapped[int | None] = mapped_column(
        ForeignKey("audits.id", ondelete="CASCADE"), default=None, index=True
    )
    kind: Mapped[str] = mapped_column(String(24), default="audit_report")
    fmt: Mapped[str] = mapped_column(String(8))
    version: Mapped[int] = mapped_column(Integer, default=1)
    file_name: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(512))
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)

    audit: Mapped[Audit | None] = relationship(lazy="selectin")
    created_by: Mapped[User | None] = relationship(lazy="selectin")
