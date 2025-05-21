"""LLM-assisted enrichment stage.

Contract with the rest of the pipeline
--------------------------------------
* Runs **after** deterministic analysis and can only *add* candidate
  observations with classifications ``possible_deviation``,
  ``insufficient_evidence`` or ``sop_conflict``.
* It can never produce ``confirmed_deviation`` or ``no_detected_deviation``.
* Every observation must quote verbatim source text. Quotes that cannot be
  located in the stored documents are dropped as fabricated and counted in the
  ``unsupported_claim_rate`` metric.
* Any provider/schema failure degrades gracefully: the audit completes with
  deterministic findings and records the failure in the pipeline log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.logging_setup import get_logger
from app.models.entities import ProcessReport, Requirement, SopRevision
from app.models.enums import Detector, EvidenceSufficiency, FindingClassification, Severity
from app.services.audit.comparison import CitationDraft, FindingDraft
from app.services.llm.base import LLMAdapter, LLMError, LLMResult
from app.services.llm.prompts import OBSERVATION_SCHEMA, SYSTEM_PROMPT, build_user_prompt
from app.services.llm.service import complete_with_cache
from app.services.llm.validation import SchemaValidationError, parse_and_validate
from app.utils.text import find_span, normalize_whitespace, truncate

logger = get_logger(__name__)

MAX_EXCERPT_CHARS = 9000

ALLOWED_CLASSIFICATIONS = {
    "possible_deviation": FindingClassification.POSSIBLE_DEVIATION,
    "insufficient_evidence": FindingClassification.INSUFFICIENT_EVIDENCE,
    "sop_conflict": FindingClassification.SOP_CONFLICT,
}


@dataclass
class LLMStageResult:
    drafts: list[FindingDraft] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    provider: str | None = None
    latency_ms: int = 0
    ok: bool = True
    error: str | None = None
    rejected_observations: int = 0
    total_observations: int = 0
    schema_valid: bool = True
    cached: bool = False
    raw_result: LLMResult | None = None


def _excerpt(text: str, limit: int = MAX_EXCERPT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.3) :]
    return f"{head}\n...[{len(text) - limit} characters omitted]...\n{tail}"


def _requirement_catalogue(requirements: list[Requirement]) -> list[dict[str, Any]]:
    return [
        {
            "key": r.req_key,
            "step": r.step_number,
            "obligation": r.obligation,
            "text": truncate(r.text, 180),
            "parameter": r.parameter_name,
        }
        for r in requirements
    ]


def _locate_citation(
    quote: str, revision: SopRevision | None, report: ProcessReport, source: str
) -> CitationDraft | None:
    """Resolve a model-supplied quote to real offsets, or reject it."""
    quote_clean = normalize_whitespace(quote)
    if len(quote_clean) < 8:
        return None
    if source == "sop":
        if revision is None:
            return None
        span = find_span(revision.raw_text, quote_clean)
        if span is None:
            return None
        label = (
            f"{revision.document.sop_code} {revision.revision_code}"
            if revision.document
            else revision.revision_code
        )
        return CitationDraft(
            source_type="sop",
            document_label=label,
            quote=revision.raw_text[span[0] : span[1]],
            char_start=span[0],
            char_end=span[1],
            revision_id=revision.id,
        )
    span = find_span(report.raw_text, quote_clean)
    if span is None:
        return None
    return CitationDraft(
        source_type="report",
        document_label=report.report_code,
        quote=report.raw_text[span[0] : span[1]],
        char_start=span[0],
        char_end=span[1],
        report_id=report.id,
    )


async def run_llm_stage(
    adapter: LLMAdapter,
    *,
    revision: SopRevision | None,
    report: ProcessReport,
    requirements: list[Requirement],
    deterministic_summary: list[str],
    use_cache: bool = True,
) -> LLMStageResult:
    """Execute the optional LLM stage; never raises into the pipeline."""
    out = LLMStageResult(provider=adapter.provider_type, model=adapter.settings.model or None)
    sop_label = (
        f"{revision.document.sop_code} {revision.revision_code}"
        if revision is not None and revision.document
        else "unresolved"
    )
    user_prompt = build_user_prompt(
        sop_label=sop_label,
        sop_excerpt=_excerpt(revision.raw_text)
        if revision is not None
        else "(no governing revision resolved)",
        report_label=report.report_code,
        report_excerpt=_excerpt(report.raw_text),
        deterministic_summary=deterministic_summary,
        requirement_catalogue=_requirement_catalogue(requirements),
    )

    try:
        result = await complete_with_cache(adapter, SYSTEM_PROMPT, user_prompt, use_cache=use_cache)
    except LLMError as exc:
        out.ok = False
        out.error = f"{exc.__class__.__name__}: {exc}"
        out.notes.append(
            "LLM stage unavailable; the audit completed with deterministic analysis only. "
            f"Reason: {exc.user_message}"
        )
        logger.warning("LLM stage failed: %s", exc)
        return out

    out.raw_result = result
    out.usage = result.usage.to_dict()
    out.latency_ms = result.latency_ms
    out.model = result.model
    out.cached = result.cached

    try:
        payload = parse_and_validate(result.text, OBSERVATION_SCHEMA)
    except SchemaValidationError as exc:
        out.ok = False
        out.schema_valid = False
        out.error = f"SchemaValidationError: {exc}"
        out.notes.append(
            "The model response did not satisfy the required JSON schema and was discarded. "
            "Deterministic findings are unaffected."
        )
        logger.warning("LLM schema validation failed: %s", exc)
        return out

    out.notes.extend(payload.get("notes", []))
    observations = payload.get("observations", [])
    out.total_observations = len(observations)

    for observation in observations:
        citations: list[CitationDraft] = []
        for evidence in observation.get("evidence", []):
            citation = _locate_citation(
                evidence.get("quote", ""), revision, report, evidence.get("source", "report")
            )
            if citation is not None:
                citations.append(citation)
        if not citations:
            out.rejected_observations += 1
            logger.info(
                "Rejected LLM observation with unverifiable evidence: %s",
                truncate(observation.get("title", ""), 80),
            )
            continue

        classification = ALLOWED_CLASSIFICATIONS.get(
            observation.get("classification", ""), FindingClassification.INSUFFICIENT_EVIDENCE
        )
        severity = observation.get("severity", Severity.MINOR)
        if severity not in {Severity.CRITICAL, Severity.MAJOR, Severity.MINOR, Severity.INFO}:
            severity = Severity.MINOR
        requirement = next((r for r in requirements if r.req_key == observation.get("requirement_key")), None)
        out.drafts.append(
            FindingDraft(
                category=observation.get("category", "ambiguous_requirement"),
                classification=classification,
                severity=severity,
                title=truncate(observation.get("title", "AI-assisted observation"), 160),
                expected=observation.get("expected", ""),
                observed=observation.get("observed", ""),
                explanation=(
                    observation.get("explanation", "")
                    + "\n\n[AI-assisted observation — requires verification. "
                    "Deterministic checks are unaffected by this item.]"
                ),
                suggested_action=observation.get("suggested_action", "Review the cited evidence and decide."),
                requirement=requirement,
                evidence_sufficiency=EvidenceSufficiency.PARTIAL,
                confidence=float(observation.get("confidence", 0.5) or 0.5),
                detector=Detector.LLM,
                citations=citations,
                uncertainty_notes=[
                    "Generated by a language model; evidence re-verified against source text."
                ],
                metrics={"llm_model": result.model, "llm_provider": result.provider},
            )
        )

    if out.rejected_observations:
        out.notes.append(
            f"{out.rejected_observations} of {out.total_observations} model observations were discarded "
            "because their quoted evidence could not be located in the source documents."
        )
    return out
