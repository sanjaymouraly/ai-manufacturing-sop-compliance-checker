"""The audit pipeline.

Ingestion → Parsing → Version Resolution → Requirement Extraction →
Evidence Retrieval → Compliance Comparison → Contradiction Detection →
Verification → Risk Classification → (Human Review) → Report Generation

Stages are explicit and every stage appends to ``audit.pipeline_log`` so the run
is reproducible and inspectable after the fact.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_setup import get_logger
from app.models.entities import (
    ApiAuditEvent,
    Audit,
    AuditFinding,
    Citation,
    ProcessReport,
    ReportEvent,
    Requirement,
    SopConflict,
    SopDocument,
    SopRevision,
)
from app.models.enums import (
    AnalysisMode,
    AuditStatus,
    EvidenceSufficiency,
    FindingCategory,
    FindingClassification,
    ReviewStatus,
    RevisionStatus,
    Severity,
)
from app.services.audit import conflicts as conflict_engine
from app.services.audit import risk
from app.services.audit.comparison import (
    CitationDraft,
    FindingDraft,
    check_duplicates,
    check_extra_events,
    check_internal_contradictions,
    check_missing_and_status,
    check_parameter,
    check_prohibited_actions,
    check_record_keeping,
    check_sequence,
    check_unsupported_claims,
)
from app.services.audit.matching import match_requirements
from app.services.audit.verifier import verification_summary, verify_finding
from app.services.versioning.resolver import resolve_version
from app.utils.text import truncate

logger = get_logger(__name__)

ENGINE_VERSION = "1.0.0"


class AuditError(RuntimeError):
    pass


def _next_audit_code(db: Session) -> str:
    count = db.scalar(select(func.count(Audit.id))) or 0
    return f"AUD-{datetime.now(UTC).strftime('%Y%m%d')}-{count + 1:04d}"


def _stage(log: list[dict[str, Any]], name: str, started: float, **details: Any) -> None:
    log.append(
        {
            "stage": name,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "at": datetime.now(UTC).isoformat(),
            **details,
        }
    )


def _injection_findings(report: ProcessReport, revision: SopRevision | None) -> list[FindingDraft]:
    drafts: list[FindingDraft] = []
    sources: list[tuple[str, Any, list[dict[str, Any]]]] = [("report", report, report.injection_flags or [])]
    if revision is not None:
        sources.append(("sop", revision, revision.injection_flags or []))

    for source_type, entity, flags in sources:
        if not flags:
            continue
        if isinstance(entity, ProcessReport):
            label = entity.report_code
        else:
            label = f"{entity.document.sop_code} {entity.revision_code}"
        citations = [
            CitationDraft(
                source_type=source_type,
                document_label=label,
                quote=flag.get("quote", ""),
                char_start=int(flag.get("char_start", 0)),
                char_end=int(flag.get("char_end", 0)),
                report_id=entity.id if source_type == "report" else None,
                revision_id=entity.id if source_type == "sop" else None,
            )
            for flag in flags[:3]
        ]
        patterns = sorted({flag.get("pattern", "unknown") for flag in flags})
        drafts.append(
            FindingDraft(
                category=FindingCategory.PROMPT_INJECTION,
                classification=FindingClassification.POSSIBLE_DEVIATION,
                severity=Severity.MAJOR,
                title=f"Embedded instruction-injection content detected in the {source_type}",
                expected="Documents must contain procedural/record content only, not instructions to the analysis system.",
                observed=f"{len(flags)} suspicious span(s) detected ({', '.join(patterns)}).",
                explanation=(
                    "Text that attempts to influence the automated analysis was found in the uploaded "
                    "document. The content was treated strictly as untrusted data and was neutralised "
                    "before any model call. The document's integrity should be investigated."
                ),
                suggested_action=(
                    "Verify the document's provenance and authorisation; confirm the record was not tampered with."
                ),
                evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
                confidence=0.9,
                citations=citations,
                metrics={"patterns": patterns, "hits": len(flags)},
            )
        )
    return drafts


#: Substrings that mark a warning as being about text extraction fidelity rather than
#: about document metadata (approval, dates, applicability).
_EXTRACTION_WARNING_HINTS = (
    "text layer",
    "ocr",
    "extract",
    "unreadable",
    "garbled",
    "could not be read",
    "encrypted",
    "image",
    "page ",
    "truncated",
    "decode",
)


def _is_extraction_warning(warning: str) -> bool:
    lowered = (warning or "").lower()
    return any(hint in lowered for hint in _EXTRACTION_WARNING_HINTS)


def _declared_candidate(revisions: list[SopRevision], report: ProcessReport) -> SopRevision | None:
    """Best-effort pick of the revision an unresolved audit was probably about."""
    if not revisions:
        return None
    declared = (report.declared_revision_code or "").strip().lower()
    if declared:
        for revision in revisions:
            if (revision.revision_code or "").strip().lower() == declared:
                return revision
    if len(revisions) == 1:
        return revisions[0]
    return None


def _extraction_quality_findings(report: ProcessReport, revision: SopRevision | None) -> list[FindingDraft]:
    drafts: list[FindingDraft] = []
    for entity in (report, revision):
        if entity is None:
            continue
        # Only genuine *extraction* problems belong here. Metadata warnings (e.g. "status
        # claims approved but no approver is recorded") also live in extraction_warnings,
        # but they say nothing about text fidelity and are already escalated as SOP
        # conflicts - reporting them as degraded extraction would be a false claim.
        extraction_warnings = [w for w in (entity.extraction_warnings or []) if _is_extraction_warning(w)]
        degraded = entity.extraction_confidence < 0.95 or entity.ocr_used
        if not degraded:
            continue
        if isinstance(entity, ProcessReport):
            label = entity.report_code
        else:
            label = f"{entity.document.sop_code} {entity.revision_code}"
        drafts.append(
            FindingDraft(
                category=FindingCategory.EXTRACTION_QUALITY,
                classification=FindingClassification.INSUFFICIENT_EVIDENCE,
                severity=Severity.MINOR if entity.extraction_confidence >= 0.6 else Severity.MAJOR,
                title=f"Reduced extraction quality for {label}",
                expected="Complete, machine-readable source content.",
                observed=(
                    f"Extraction confidence {entity.extraction_confidence:.0%}"
                    + ("; OCR used" if entity.ocr_used else "")
                    + (f"; {len(extraction_warnings)} warning(s)" if extraction_warnings else "")
                ),
                explanation=(
                    "Parts of this document could not be extracted reliably: "
                    + ("; ".join(extraction_warnings[:4]) or "low extraction confidence")
                    + ". Conclusions drawn from this source may be incomplete; no text was inferred to fill gaps."
                ),
                suggested_action="Provide a text-based version of the document or verify the affected pages manually.",
                evidence_sufficiency=EvidenceSufficiency.PARTIAL,
                confidence=0.9,
                citations=[],
                uncertainty_notes=extraction_warnings[:6],
                metrics={"extraction_confidence": entity.extraction_confidence, "ocr_used": entity.ocr_used},
            )
        )
    return drafts


def _conflict_findings(conflicts: list[SopConflict]) -> list[FindingDraft]:
    drafts: list[FindingDraft] = []
    for conflict in conflicts:
        citations: list[CitationDraft] = []
        for evidence in (conflict.evidence_a, conflict.evidence_b):
            if not evidence or not evidence.get("quote"):
                continue
            citations.append(
                CitationDraft(
                    source_type="sop",
                    document_label=evidence.get("document_label", ""),
                    quote=evidence.get("quote", ""),
                    char_start=int(evidence.get("char_start", 0)),
                    char_end=int(evidence.get("char_end", 0)),
                    revision_id=evidence.get("revision_id"),
                    section_path=evidence.get("section_path"),
                    page=evidence.get("page"),
                )
            )
        drafts.append(
            FindingDraft(
                category=FindingCategory.CONFLICTING_REVISION,
                classification=FindingClassification.SOP_CONFLICT,
                severity=conflict.severity,
                title=conflict.title,
                expected="A single, unambiguous authoritative instruction for this process.",
                observed=conflict.description,
                explanation=(
                    f"Conflict type '{conflict.conflict_type}' ({conflict.conflict_code}). "
                    + (
                        "This conflict blocks a definitive compliance conclusion and has been escalated "
                        "for human review."
                        if conflict.blocks_conclusion
                        else "This is a documentation-metadata defect; it does not by itself block the audit."
                    )
                ),
                suggested_action=(
                    "Resolve the conflict in the SOP Conflict Center with a documented rationale, then re-run the audit."
                ),
                evidence_sufficiency=EvidenceSufficiency.PARTIAL,
                confidence=1.0,
                citations=citations,
                uncertainty_notes=["Authoritative requirement is ambiguous while this conflict is open."],
                metrics={"conflict_code": conflict.conflict_code, "conflict_type": conflict.conflict_type},
                conflict_id=conflict.id,
            )
        )
    return drafts


def _version_findings(
    resolution: Any, report: ProcessReport, revisions: list[SopRevision], revision: SopRevision | None
) -> list[FindingDraft]:
    drafts: list[FindingDraft] = []
    declared_code = report.declared_revision_code
    if not declared_code or revision is None:
        return drafts

    def _declaration_citation() -> list[CitationDraft]:
        """Quote the place in the report where the revision is declared."""
        from app.utils.text import find_span

        for needle in (
            f'"sop_revision": "{declared_code}"',
            f'"sop_revision": {declared_code}',
            f'"declared_revision_code": "{declared_code}"',
            f"SOP Revision: {declared_code}",
            f"Revision: {declared_code}",
            f"Rev {declared_code}",
        ):
            span = find_span(report.raw_text, needle)
            if span:
                return [
                    CitationDraft(
                        source_type="report",
                        document_label=report.report_code,
                        quote=report.raw_text[span[0] : span[1]],
                        char_start=span[0],
                        char_end=span[1],
                        report_id=report.id,
                        line_ref="revision declaration",
                    )
                ]
        return []

    declaration_citations = _declaration_citation()
    declared = next(
        (r for r in revisions if r.revision_code.strip().lower() == declared_code.strip().lower()), None
    )
    if declared is None:
        drafts.append(
            FindingDraft(
                category=FindingCategory.OUTDATED_SOP,
                classification=FindingClassification.CONFIRMED_DEVIATION,
                severity=Severity.MAJOR,
                title=f"Report cites revision '{declared_code}', which is not registered",
                expected=f"Execution against the governing revision {revision.revision_code}.",
                observed=f"The report states it followed revision '{declared_code}'.",
                explanation=(
                    "The cited revision does not exist in the document library, so the instructions actually "
                    "used cannot be verified."
                ),
                suggested_action="Confirm which document the operator used and register it if it is legitimate.",
                evidence_sufficiency=EvidenceSufficiency.PARTIAL,
                confidence=0.9,
                citations=declaration_citations,
                metrics={"declared": declared_code, "resolved": revision.revision_code},
            )
        )
        return drafts

    if declared.id == revision.id:
        return drafts

    severity = (
        Severity.CRITICAL
        if declared.status in {RevisionStatus.SUPERSEDED, RevisionStatus.ARCHIVED, RevisionStatus.DRAFT}
        else Severity.MAJOR
    )
    drafts.append(
        FindingDraft(
            category=FindingCategory.OUTDATED_SOP,
            classification=FindingClassification.CONFIRMED_DEVIATION,
            severity=severity,
            title=f"Execution recorded against non-governing revision {declared.revision_code}",
            expected=(
                f"Revision {revision.revision_code} (status {revision.status}, effective "
                f"{revision.effective_date}) governs this execution."
            ),
            observed=f"The report cites revision {declared.revision_code} (status {declared.status}).",
            explanation=(
                f"The report states it followed revision {declared.revision_code}, but version resolution "
                f"determined that {revision.revision_code} was authoritative on "
                f"{report.executed_at or 'the execution date'}. Requirements may differ between the two."
            ),
            suggested_action=(
                "Compare the two revisions, confirm which instructions the operator actually followed and "
                "assess product impact."
            ),
            evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
            confidence=0.95,
            citations=declaration_citations,
            metrics={
                "declared": declared.revision_code,
                "declared_status": declared.status,
                "resolved": revision.revision_code,
            },
        )
    )
    return drafts


def _deterministic_summary(drafts: list[FindingDraft]) -> list[str]:
    return [
        f"[{d.classification}/{d.severity}] {truncate(d.title, 110)}"
        for d in drafts
        if d.classification != FindingClassification.NO_DETECTED_DEVIATION
    ][:25]


async def run_audit(
    db: Session,
    report: ProcessReport,
    *,
    mode: str | None = None,
    actor_id: int | None = None,
    use_llm_cache: bool = True,
) -> Audit:
    """Execute the full audit pipeline for one process report."""
    settings = get_settings()
    mode = mode or settings.analysis_mode
    started_at = datetime.now(UTC)
    t_total = time.perf_counter()
    pipeline_log: list[dict[str, Any]] = []

    audit = Audit(
        audit_code=_next_audit_code(db),
        report_id=report.id,
        status=AuditStatus.RUNNING,
        mode=mode,
        started_at=started_at,
        created_by_id=actor_id,
        engine_version=ENGINE_VERSION,
        input_snapshot={
            "report_id": report.id,
            "report_code": report.report_code,
            "report_sha256": report.file_hash,
            "report_events": len(report.events),
            "settings": {
                "evidence_match_threshold": settings.evidence_match_threshold,
                "strict_citation_verification": settings.strict_citation_verification,
                "engine_version": ENGINE_VERSION,
            },
        },
    )
    db.add(audit)
    db.flush()

    try:
        # ---------------- Stage 1: applicable document ----------------
        t = time.perf_counter()
        document = None
        if report.declared_sop_code:
            document = db.scalar(select(SopDocument).where(SopDocument.sop_code == report.declared_sop_code))
        revisions: list[SopRevision] = list(document.revisions) if document else []
        _stage(
            pipeline_log,
            "document_lookup",
            t,
            sop_code=report.declared_sop_code,
            found=document is not None,
            revisions=len(revisions),
        )

        # ---------------- Stage 2: version resolution ----------------
        t = time.perf_counter()
        resolution = resolve_version(revisions, report)
        revision = (
            db.get(SopRevision, resolution.resolved_revision_id) if resolution.resolved_revision_id else None
        )
        audit.resolved_revision_id = revision.id if revision else None
        audit.version_resolution = resolution.to_dict()
        if document is None:
            resolution.blocked = True
            resolution.requires_human_review = True
            resolution.notes.append(
                f"No SOP document matching '{report.declared_sop_code or 'unspecified'}' exists in the library."
            )
            audit.version_resolution = resolution.to_dict()
        _stage(
            pipeline_log,
            "version_resolution",
            t,
            resolved=resolution.resolved_revision_code,
            basis=resolution.authority_basis,
            blocked=resolution.blocked,
        )

        # ---------------- Stage 3: conflict detection ----------------
        t = time.perf_counter()
        detected_conflicts: list[SopConflict] = []
        if document is not None:
            detected_conflicts = conflict_engine.scan_document(db, document)
        # Cross-document conflicts are produced by a library-wide scan rather than by the
        # single-document scan above, so merge any already-recorded open conflicts that
        # touch the revisions of this document (e.g. SOP-WLD-700 vs SOP-EQP-315).
        known_ids = {c.id for c in detected_conflicts if c.id is not None}
        revision_ids = [r.id for r in revisions if r.id is not None]
        if revision_ids:
            for existing in conflict_engine.open_conflicts_for_revisions(db, revision_ids):
                if existing.id not in known_ids:
                    detected_conflicts.append(existing)
                    known_ids.add(existing.id)
        relevant_conflicts = [c for c in detected_conflicts if c.status in {"open", "escalated"}]
        if revision is not None:
            relevant_conflicts = [
                c
                for c in relevant_conflicts
                if c.revision_a_id in {revision.id, None}
                or c.revision_b_id in {revision.id, None}
                or c.blocks_conclusion
            ]
        conflict_drafts = _conflict_findings(relevant_conflicts)
        blocking_conflict = any(c.blocks_conclusion for c in relevant_conflicts)
        _stage(
            pipeline_log,
            "conflict_detection",
            t,
            conflicts=len(relevant_conflicts),
            blocking=blocking_conflict,
        )

        # ---------------- Stage 4: requirements & events ----------------
        t = time.perf_counter()
        requirements: list[Requirement] = []
        if revision is not None:
            requirements = list(
                db.scalars(
                    select(Requirement)
                    .where(Requirement.revision_id == revision.id)
                    .order_by(Requirement.seq)
                )
            )
        events: list[ReportEvent] = list(
            db.scalars(
                select(ReportEvent).where(ReportEvent.report_id == report.id).order_by(ReportEvent.seq)
            )
        )
        _stage(pipeline_log, "requirement_extraction", t, requirements=len(requirements), events=len(events))

        # ---------------- Stage 5: evidence retrieval ----------------
        t = time.perf_counter()
        matches = match_requirements(requirements, events, threshold=settings.evidence_match_threshold)
        matched_count = sum(1 for m in matches.values() if m.matched)
        _stage(
            pipeline_log,
            "evidence_retrieval",
            t,
            matched=matched_count,
            unmatched=len(requirements) - matched_count,
            threshold=settings.evidence_match_threshold,
        )

        # ---------------- Stage 6: deterministic comparison ----------------
        t = time.perf_counter()
        drafts: list[FindingDraft] = []
        report_is_structured = (report.source_format or "") in {"json", "csv"}
        if revision is not None:
            for requirement in requirements:
                match = matches[requirement.id]
                drafts += check_missing_and_status(
                    requirement,
                    match,
                    revision,
                    report,
                    report_is_structured=report_is_structured,
                    all_events=events,
                )
                drafts += check_parameter(requirement, match, revision, report)
                drafts += check_record_keeping(requirement, match, revision, report)
            drafts += check_sequence(requirements, matches, revision, report)
            drafts += check_duplicates(requirements, events, matches, revision, report)
            prohibited_drafts, consumed_events = check_prohibited_actions(
                requirements, events, revision, report
            )
            drafts += prohibited_drafts
            drafts += check_extra_events(events, matches, report, exclude_event_ids=consumed_events)
        drafts += check_internal_contradictions(report, events)
        _stage(pipeline_log, "compliance_comparison", t, findings=len(drafts))

        # ---------------- Stage 7: contradictions, claims, integrity ----------------
        t = time.perf_counter()
        drafts += check_unsupported_claims(report, events, drafts, revision)
        drafts += _injection_findings(report, revision)
        # Report extraction quality against the governing revision when there is one, and
        # otherwise against the candidate the report declares: an unreadable scanned SOP is
        # a fact the reviewer needs whether or not authority could be established.
        quality_revision = revision or _declared_candidate(revisions, report)
        drafts += _extraction_quality_findings(report, quality_revision)
        drafts += _version_findings(resolution, report, revisions, revision)
        drafts += conflict_drafts
        _stage(pipeline_log, "contradiction_detection", t, total_findings=len(drafts))

        # ---------------- Stage 8: optional LLM assistance ----------------
        llm_notes: list[str] = []
        if mode == AnalysisMode.LLM_ASSISTED:
            t = time.perf_counter()
            from app.services.llm.analyzer import run_llm_stage
            from app.services.llm.service import get_active_config, get_adapter, record_spend

            adapter = get_adapter(db)
            stage = await run_llm_stage(
                adapter,
                revision=revision,
                report=report,
                requirements=requirements,
                deterministic_summary=_deterministic_summary(drafts),
                use_cache=use_llm_cache,
            )
            drafts += stage.drafts
            llm_notes = stage.notes
            audit.provider_used = stage.provider
            audit.model_used = stage.model
            audit.token_usage = stage.usage
            audit.cost_usd = float(stage.usage.get("cost_usd") or 0.0)
            if stage.raw_result is not None:
                record_spend(db, get_active_config(db), stage.raw_result)
            _stage(
                pipeline_log,
                "llm_assistance",
                t,
                ok=stage.ok,
                provider=stage.provider,
                model=stage.model,
                observations=stage.total_observations,
                rejected=stage.rejected_observations,
                schema_valid=stage.schema_valid,
                cached=stage.cached,
                error=stage.error,
            )
        else:
            audit.provider_used = "offline"
            audit.model_used = "deterministic-rule-engine"

        # ---------------- Stage 9: verification ----------------
        t = time.perf_counter()
        revision_map: dict[int, SopRevision] = {r.id: r for r in revisions}
        # A cross-document conflict legitimately quotes the *other* SOP, which is not one of
        # this document's revisions. Those revisions are part of the audit's evidence base,
        # so they must be loadable by the verifier - otherwise a perfectly valid quote would
        # be marked as an unverifiable citation.
        for conflict in relevant_conflicts:
            for related_id in (conflict.revision_a_id, conflict.revision_b_id):
                if related_id and related_id not in revision_map:
                    related = db.get(SopRevision, related_id)
                    if related is not None:
                        revision_map[related_id] = related
        report_map = {report.id: report}
        outcomes = [
            verify_finding(d, revision_map, report_map, strict=settings.strict_citation_verification)
            for d in drafts
        ]
        verify_stats = verification_summary(outcomes)
        _stage(pipeline_log, "verification", t, **verify_stats)

        # ---------------- Stage 10: risk classification ----------------
        t = time.perf_counter()
        # "Authority blocked" means the governing revision itself could not be established.
        # A material conflict that does not prevent resolution still forces human review, but
        # it is reported through the SOP_CONFLICT findings rather than by voiding the audit.
        authority_blocked = bool(resolution.blocked)
        summary = risk.summarise(drafts, authority_blocked=authority_blocked)
        summary["verification"] = verify_stats
        summary["matched_requirements"] = matched_count
        summary["total_requirements"] = len(requirements)
        summary["llm_notes"] = llm_notes
        summary["authority_basis"] = resolution.authority_basis
        _stage(pipeline_log, "risk_classification", t, outcome=summary["outcome"])

        # ---------------- Stage 11: persistence ----------------
        t = time.perf_counter()
        for index, (draft, outcome) in enumerate(zip(drafts, outcomes, strict=False), start=1):
            finding = AuditFinding(
                audit_id=audit.id,
                finding_code=f"{audit.audit_code}-F{index:03d}",
                classification=draft.classification,
                category=draft.category,
                severity=draft.severity,
                requirement_id=draft.requirement.id if draft.requirement else None,
                requirement_key=draft.requirement.req_key if draft.requirement else None,
                sop_code=document.sop_code if document else None,
                revision_code=revision.revision_code if revision else None,
                title=truncate(draft.title, 255),
                expected=draft.expected,
                observed=draft.observed,
                explanation=draft.explanation,
                suggested_action=draft.suggested_action,
                evidence_sufficiency=draft.evidence_sufficiency,
                confidence=round(draft.confidence, 4),
                detector=draft.detector,
                verification_status=outcome.status,
                verification_notes=outcome.notes,
                uncertainty_notes=draft.uncertainty_notes,
                metrics=draft.metrics,
                conflict_id=draft.conflict_id,
                review_status=ReviewStatus.OPEN,
                original_payload={
                    "classification": draft.classification,
                    "severity": draft.severity,
                    "title": draft.title,
                    "expected": draft.expected,
                    "observed": draft.observed,
                    "explanation": draft.explanation,
                    "detector": draft.detector,
                    "confidence": draft.confidence,
                    "generated_at": datetime.now(UTC).isoformat(),
                },
            )
            db.add(finding)
            db.flush()
            for citation_draft, verification in zip(draft.citations, outcome.citations, strict=False):
                db.add(
                    Citation(
                        finding_id=finding.id,
                        source_type=citation_draft.source_type,
                        revision_id=citation_draft.revision_id,
                        report_id=citation_draft.report_id,
                        document_label=citation_draft.document_label,
                        section_path=citation_draft.section_path,
                        page=citation_draft.page,
                        line_ref=citation_draft.line_ref,
                        char_start=citation_draft.char_start,
                        char_end=citation_draft.char_end,
                        quote=citation_draft.quote,
                        verified=verification.verified,
                        verification_note=verification.note[:255] if verification.note else None,
                        similarity=verification.similarity,
                    )
                )

        audit.status = AuditStatus.COMPLETED
        audit.outcome = summary["outcome"]
        audit.requires_human_review = bool(summary["requires_human_review"])
        audit.review_status = ReviewStatus.OPEN
        audit.summary = summary
        audit.finished_at = datetime.now(UTC)
        audit.duration_ms = int((time.perf_counter() - t_total) * 1000)
        audit.pipeline_log = pipeline_log
        audit.input_snapshot["sop_sha256"] = revision.file_hash if revision else None
        audit.input_snapshot["resolved_revision"] = resolution.resolved_revision_code
        _stage(pipeline_log, "persistence", t, findings=len(drafts))
        audit.pipeline_log = pipeline_log

        db.add(
            ApiAuditEvent(
                event_type="audit_completed",
                actor_id=actor_id,
                entity_type="audit",
                entity_id=audit.id,
                message=f"Audit {audit.audit_code} completed: {summary['outcome']}",
                payload={
                    "findings": len(drafts),
                    "outcome": summary["outcome"],
                    "mode": mode,
                    "duration_ms": audit.duration_ms,
                    "requires_human_review": audit.requires_human_review,
                },
            )
        )
        db.flush()
        logger.info(
            "Audit %s completed in %dms: %s (%d findings)",
            audit.audit_code,
            audit.duration_ms,
            summary["outcome"],
            len(drafts),
        )
        return audit

    except Exception as exc:  # pragma: no cover - defensive, surfaced to the API
        logger.exception("Audit %s failed", audit.audit_code)
        audit.status = AuditStatus.FAILED
        audit.error_message = f"{exc.__class__.__name__}: {exc}"
        audit.finished_at = datetime.now(UTC)
        audit.duration_ms = int((time.perf_counter() - t_total) * 1000)
        audit.pipeline_log = pipeline_log
        db.flush()
        raise AuditError(str(exc)) from exc
