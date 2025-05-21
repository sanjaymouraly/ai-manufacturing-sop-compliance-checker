"""Audit report generation: JSON, CSV and PDF.

Every export carries the decision-support disclaimer, the resolved SOP
revision, evidence with verification status, unresolved conflicts and reviewer
state. Exports are versioned per audit and content-hashed.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.entities import (
    Audit,
    AuditFinding,
    Citation,
    ExportArtifact,
    ProcessReport,
    ReviewAction,
    SopConflict,
    SopRevision,
    User,
)
from app.models.enums import ConflictStatus
from app.services.audit.risk import DISCLAIMER, OUTCOME_LABELS
from app.utils.text import sha256_bytes

GENERATOR_VERSION = "1.0.0"


def _outcome_label(outcome: object) -> str | None:
    """Human-readable outcome name; falls back to the raw value if unmapped."""
    if outcome is None:
        return None
    for key, label in OUTCOME_LABELS.items():
        if key == outcome or getattr(key, "value", key) == outcome:
            return label
    return str(outcome)


def build_audit_payload(db: Session, audit: Audit) -> dict[str, Any]:
    """Canonical machine-readable audit report."""
    report = db.get(ProcessReport, audit.report_id)
    revision = db.get(SopRevision, audit.resolved_revision_id) if audit.resolved_revision_id else None
    findings = list(
        db.scalars(select(AuditFinding).where(AuditFinding.audit_id == audit.id).order_by(AuditFinding.id))
    )
    finding_ids = [f.id for f in findings] or [-1]
    citations = list(db.scalars(select(Citation).where(Citation.finding_id.in_(finding_ids))))
    citations_by_finding: dict[int, list[Citation]] = {}
    for citation in citations:
        citations_by_finding.setdefault(citation.finding_id, []).append(citation)

    conflict_ids = [f.conflict_id for f in findings if f.conflict_id]
    conflicts = (
        list(db.scalars(select(SopConflict).where(SopConflict.id.in_(conflict_ids)))) if conflict_ids else []
    )
    actions = list(
        db.scalars(
            select(ReviewAction)
            .where(
                ((ReviewAction.entity_type == "finding") & (ReviewAction.entity_id.in_(finding_ids)))
                | ((ReviewAction.entity_type == "audit") & (ReviewAction.entity_id == audit.id))
            )
            .order_by(ReviewAction.created_at)
        )
    )
    reviewer = db.get(User, audit.assigned_reviewer_id) if audit.assigned_reviewer_id else None

    return {
        "schema_version": "1.0",
        "generator": f"manufacturing-sop-ai-auditor/{GENERATOR_VERSION}",
        "generated_at": datetime.now(UTC).isoformat(),
        "disclaimer": DISCLAIMER,
        "audit": {
            "audit_code": audit.audit_code,
            "status": audit.status,
            "outcome": audit.outcome,
            "outcome_label": _outcome_label(audit.outcome),
            "analysis_mode": audit.mode,
            "provider_used": audit.provider_used,
            "model_used": audit.model_used,
            "engine_version": audit.engine_version,
            "started_at": audit.started_at.isoformat() if audit.started_at else None,
            "finished_at": audit.finished_at.isoformat() if audit.finished_at else None,
            "duration_ms": audit.duration_ms,
            "requires_human_review": audit.requires_human_review,
            "review_status": audit.review_status,
            "assigned_reviewer": reviewer.username if reviewer else None,
            "token_usage": audit.token_usage,
            "cost_usd": audit.cost_usd,
            "input_snapshot": audit.input_snapshot,
            "pipeline_log": audit.pipeline_log,
        },
        "process_report": {
            "report_code": report.report_code if report else None,
            "title": report.title if report else None,
            "batch_id": report.batch_id if report else None,
            "line_id": report.line_id if report else None,
            "product_code": report.product_code if report else None,
            "site": report.site if report else None,
            "operator": report.operator if report else None,
            "executed_at": report.executed_at.isoformat() if report and report.executed_at else None,
            "declared_sop_code": report.declared_sop_code if report else None,
            "declared_revision_code": report.declared_revision_code if report else None,
            "file_name": report.file_name if report else None,
            "sha256": report.file_hash if report else None,
            "extraction_confidence": report.extraction_confidence if report else None,
        },
        "applied_sop": {
            "sop_code": revision.document.sop_code if revision and revision.document else None,
            "revision_code": revision.revision_code if revision else None,
            "status": revision.status if revision else None,
            "effective_date": revision.effective_date.isoformat()
            if revision and revision.effective_date
            else None,
            "expiry_date": revision.expiry_date.isoformat() if revision and revision.expiry_date else None,
            "approved_by": revision.approved_by if revision else None,
            "sha256": revision.file_hash if revision else None,
            "file_name": revision.file_name if revision else None,
        },
        "version_resolution": audit.version_resolution,
        "summary": audit.summary,
        "findings": [
            {
                "finding_code": f.finding_code,
                "classification": f.classification,
                "category": f.category,
                "severity": f.severity,
                "title": f.title,
                "requirement_key": f.requirement_key,
                "expected": f.expected,
                "observed": f.observed,
                "explanation": f.explanation,
                "suggested_action": f.suggested_action,
                "evidence_sufficiency": f.evidence_sufficiency,
                "confidence": f.confidence,
                "detector": f.detector,
                "verification_status": f.verification_status,
                "verification_notes": f.verification_notes,
                "uncertainty_notes": f.uncertainty_notes,
                "metrics": f.metrics,
                "review_status": f.review_status,
                "reviewer_comment": f.reviewer_comment,
                "reviewed_at": f.reviewed_at.isoformat() if f.reviewed_at else None,
                "original_machine_output": f.original_payload,
                "citations": [
                    {
                        "source_type": c.source_type,
                        "document": c.document_label,
                        "section_path": c.section_path,
                        "page": c.page,
                        "line_ref": c.line_ref,
                        "char_start": c.char_start,
                        "char_end": c.char_end,
                        "quote": c.quote,
                        "verified": c.verified,
                        "verification_note": c.verification_note,
                    }
                    for c in citations_by_finding.get(f.id, [])
                ],
            }
            for f in findings
        ],
        "unresolved_conflicts": [
            {
                "conflict_code": c.conflict_code,
                "type": c.conflict_type,
                "severity": c.severity,
                "status": c.status,
                "title": c.title,
                "description": c.description,
                "blocks_conclusion": c.blocks_conclusion,
                "evidence_a": c.evidence_a,
                "evidence_b": c.evidence_b,
            }
            for c in conflicts
            if c.status in {ConflictStatus.OPEN, ConflictStatus.ESCALATED}
        ],
        "decision_history": [
            {
                "at": a.created_at.isoformat(),
                "entity": f"{a.entity_type}#{a.entity_id}",
                "action": a.action,
                "from": a.from_state,
                "to": a.to_state,
                "actor": a.actor_name,
                "reason": a.reason,
            }
            for a in actions
        ],
    }


def render_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")


def render_csv(payload: dict[str, Any]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "audit_code",
            "outcome",
            "finding_code",
            "classification",
            "category",
            "severity",
            "requirement_key",
            "title",
            "expected",
            "observed",
            "explanation",
            "suggested_action",
            "evidence_sufficiency",
            "confidence",
            "detector",
            "verification_status",
            "review_status",
            "sop_code",
            "revision_code",
            "citation_count",
            "citations_verified",
            "primary_citation",
        ]
    )
    audit = payload["audit"]
    sop = payload["applied_sop"]
    for finding in payload["findings"]:
        citations = finding["citations"]
        primary = citations[0]["quote"] if citations else ""
        writer.writerow(
            [
                audit["audit_code"],
                audit["outcome"],
                finding["finding_code"],
                finding["classification"],
                finding["category"],
                finding["severity"],
                finding["requirement_key"] or "",
                finding["title"],
                finding["expected"],
                finding["observed"],
                finding["explanation"].replace("\n", " "),
                finding["suggested_action"],
                finding["evidence_sufficiency"],
                finding["confidence"],
                finding["detector"],
                finding["verification_status"],
                finding["review_status"],
                sop["sop_code"] or "",
                sop["revision_code"] or "",
                len(citations),
                sum(1 for c in citations if c["verified"]),
                primary,
            ]
        )
    writer.writerow([])
    writer.writerow(["DISCLAIMER", payload["disclaimer"]])
    return buffer.getvalue().encode("utf-8")


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
_SEVERITY_COLORS = {
    "critical": "#b91c1c",
    "major": "#c2410c",
    "minor": "#a16207",
    "info": "#1d4ed8",
}


def render_pdf(payload: dict[str, Any]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        KeepTogether,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Audit report {payload['audit']['audit_code']}",
        author="Manufacturing SOP AI Auditor",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle(
        "h1", parent=styles["Heading1"], fontSize=17, spaceAfter=4, textColor=colors.HexColor("#0f172a")
    )
    h2 = ParagraphStyle(
        "h2",
        parent=styles["Heading2"],
        fontSize=12.5,
        spaceBefore=10,
        spaceAfter=4,
        textColor=colors.HexColor("#1e293b"),
    )
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=8.8, leading=12, alignment=TA_LEFT)
    small = ParagraphStyle("small", parent=body, fontSize=7.6, textColor=colors.HexColor("#475569"))
    quote = ParagraphStyle(
        "quote",
        parent=small,
        leftIndent=8,
        borderPadding=3,
        backColor=colors.HexColor("#f1f5f9"),
        textColor=colors.HexColor("#0f172a"),
        fontName="Courier",
        fontSize=7.4,
        leading=9.5,
    )
    warn = ParagraphStyle(
        "warn",
        parent=body,
        fontSize=8.6,
        textColor=colors.HexColor("#7f1d1d"),
        backColor=colors.HexColor("#fef2f2"),
        borderPadding=6,
    )

    story: list[Any] = []
    audit = payload["audit"]
    report = payload["process_report"]
    sop = payload["applied_sop"]
    summary = payload.get("summary") or {}

    story.append(Paragraph("Manufacturing SOP Compliance — AI-Assisted Audit Report", h1))
    story.append(Paragraph(f"Audit {audit['audit_code']} · generated {payload['generated_at'][:19]}Z", small))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"<b>DECISION-SUPPORT ONLY.</b> {payload['disclaimer']}", warn))
    story.append(Spacer(1, 8))

    def kv_table(rows: list[list[str]], widths: list[float]) -> Table:
        table = Table(rows, colWidths=widths, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#475569")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#e2e8f0")),
                ]
            )
        )
        return table

    story.append(Paragraph("1. Audit context", h2))
    story.append(
        kv_table(
            [
                ["Outcome", audit.get("outcome_label") or "—"],
                [
                    "Analysis mode",
                    f"{audit['analysis_mode']} ({audit.get('provider_used') or 'offline'} / {audit.get('model_used') or 'rule engine'})",
                ],
                ["Engine version", audit["engine_version"]],
                ["Requires human review", "YES" if audit["requires_human_review"] else "No"],
                ["Review status", audit["review_status"]],
                ["Duration", f"{audit['duration_ms']} ms"],
            ],
            [38 * mm, 136 * mm],
        )
    )

    story.append(Paragraph("2. Process execution record", h2))
    story.append(
        kv_table(
            [
                ["Report", f"{report['report_code']} — {report['title'] or ''}"],
                [
                    "Batch / line / product",
                    f"{report['batch_id'] or '—'} / {report['line_id'] or '—'} / {report['product_code'] or '—'}",
                ],
                ["Site / operator", f"{report['site'] or '—'} / {report['operator'] or '—'}"],
                ["Executed at", report["executed_at"] or "not stated"],
                ["Source file", f"{report['file_name'] or '—'} (sha256 {str(report['sha256'])[:16]}…)"],
                ["Extraction confidence", f"{(report['extraction_confidence'] or 0) * 100:.0f}%"],
            ],
            [38 * mm, 136 * mm],
        )
    )

    story.append(Paragraph("3. Governing SOP revision", h2))
    story.append(
        kv_table(
            [
                ["SOP", f"{sop['sop_code'] or 'UNRESOLVED'} {sop['revision_code'] or ''}"],
                ["Status", sop["status"] or "unknown"],
                ["Effective window", f"{sop['effective_date'] or '—'} → {sop['expiry_date'] or 'open'}"],
                ["Approved by", sop["approved_by"] or "not recorded"],
                ["Authority basis", payload["version_resolution"].get("authority_basis", "—")],
                ["Source file", f"{sop['file_name'] or '—'} (sha256 {str(sop['sha256'] or '')[:16]}…)"],
            ],
            [38 * mm, 136 * mm],
        )
    )
    notes = payload["version_resolution"].get("notes") or []
    for note in notes[:6]:
        story.append(Paragraph(f"• {note}", small))

    story.append(Paragraph("4. Findings summary", h2))
    by_class = summary.get("by_classification", {})
    by_sev = summary.get("by_severity", {})
    summary_rows = [["Classification", "Count", "Severity", "Count"]]
    classes = list(by_class.items())
    sevs = list(by_sev.items())
    for index in range(max(len(classes), len(sevs))):
        left = classes[index] if index < len(classes) else ("", "")
        right = sevs[index] if index < len(sevs) else ("", "")
        summary_rows.append([str(left[0]), str(left[1]), str(right[0]), str(right[1])])
    table = Table(summary_rows, colWidths=[52 * mm, 20 * mm, 52 * mm, 20 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(table)
    verification = summary.get("verification", {})
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            f"Citations: {verification.get('citations_verified', 0)}/{verification.get('citations_total', 0)} "
            f"verified against source text ({(verification.get('citation_validity_rate', 0) * 100):.0f}%). "
            f"Items with limited evidence: {summary.get('items_with_limited_evidence', 0)}.",
            small,
        )
    )
    if summary.get("escalation_reasons"):
        story.append(Spacer(1, 4))
        story.append(Paragraph("<b>Escalation reasons</b>", small))
        for reason in summary["escalation_reasons"][:8]:
            story.append(Paragraph(f"• {reason}", small))

    if payload["unresolved_conflicts"]:
        story.append(Paragraph("5. Unresolved SOP conflicts", h2))
        for conflict in payload["unresolved_conflicts"]:
            story.append(
                Paragraph(
                    f"<b>{conflict['conflict_code']} — {conflict['title']}</b> "
                    f"({conflict['type']}, {conflict['severity']}, {conflict['status']})",
                    body,
                )
            )
            story.append(Paragraph(conflict["description"], small))
            story.append(Spacer(1, 3))

    story.append(PageBreak())
    story.append(Paragraph("6. Detailed findings", h2))
    if not payload["findings"]:
        story.append(Paragraph("No findings were recorded for this audit.", body))

    for finding in payload["findings"]:
        color = _SEVERITY_COLORS.get(finding["severity"], "#334155")
        block: list[Any] = [
            HRFlowable(
                width="100%", thickness=0.4, color=colors.HexColor("#cbd5e1"), spaceBefore=6, spaceAfter=4
            ),
            Paragraph(
                f"<font color='{color}'><b>[{finding['severity'].upper()}]</b></font> "
                f"<b>{finding['finding_code']} — {finding['title']}</b>",
                body,
            ),
            Paragraph(
                f"Classification: <b>{finding['classification']}</b> · Category: {finding['category']} · "
                f"Detector: {finding['detector']} · Evidence: {finding['evidence_sufficiency']} · "
                f"Verification: {finding['verification_status']} · Review: {finding['review_status']}",
                small,
            ),
            Spacer(1, 3),
            Paragraph(f"<b>Expected:</b> {finding['expected']}", body),
            Paragraph(f"<b>Observed:</b> {finding['observed']}", body),
            Paragraph(f"<b>Why flagged:</b> {finding['explanation']}", body),
        ]
        if finding["suggested_action"]:
            block.append(Paragraph(f"<b>Suggested review:</b> {finding['suggested_action']}", body))
        if finding["uncertainty_notes"]:
            block.append(
                Paragraph("<b>Uncertainty:</b> " + "; ".join(finding["uncertainty_notes"][:4]), small)
            )
        for citation in finding["citations"]:
            mark = "verified" if citation["verified"] else "UNVERIFIED"
            locator = citation["section_path"] or citation["line_ref"] or ""
            page = f" p.{citation['page']}" if citation["page"] else ""
            block.append(
                Paragraph(
                    f"[{citation['source_type']}] {citation['document']} {locator}{page} "
                    f"(chars {citation['char_start']}–{citation['char_end']}, {mark})",
                    small,
                )
            )
            block.append(Paragraph(citation["quote"][:600].replace("<", "&lt;"), quote))
        if finding["reviewer_comment"]:
            block.append(Paragraph(f"<b>Reviewer:</b> {finding['reviewer_comment']}", small))
        story.append(KeepTogether(block))

    if payload["decision_history"]:
        story.append(PageBreak())
        story.append(Paragraph("7. Reviewer decision history", h2))
        rows = [["When (UTC)", "Entity", "Action", "From → To", "Actor", "Reason"]]
        for entry in payload["decision_history"]:
            rows.append(
                [
                    entry["at"][:19],
                    entry["entity"],
                    entry["action"],
                    f"{entry['from'] or '—'} → {entry['to'] or '—'}",
                    entry["actor"] or "—",
                    (entry["reason"] or "")[:90],
                ]
            )
        history = Table(rows, colWidths=[30 * mm, 24 * mm, 20 * mm, 32 * mm, 20 * mm, 48 * mm], hAlign="LEFT")
        history.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 6.6),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(history)

    story.append(Spacer(1, 10))
    story.append(Paragraph(payload["disclaimer"], warn))

    def _footer(canvas, doc_):  # pragma: no cover - rendering callback
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(
            18 * mm, 10 * mm, f"{audit['audit_code']} · AI-assisted · requires human verification"
        )
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


RENDERERS = {"json": render_json, "csv": render_csv, "pdf": render_pdf}
MEDIA_TYPES = {"json": "application/json", "csv": "text/csv", "pdf": "application/pdf"}


def export_audit(db: Session, audit: Audit, fmt: str, *, created_by_id: int | None = None) -> ExportArtifact:
    """Render and persist an export artefact (versioned per audit+format)."""
    fmt = fmt.lower()
    if fmt not in RENDERERS:
        raise ValueError(f"Unsupported export format '{fmt}'. Use one of: {', '.join(RENDERERS)}.")
    payload = build_audit_payload(db, audit)
    data = RENDERERS[fmt](payload)

    version = (
        db.scalar(
            select(func.count(ExportArtifact.id)).where(
                ExportArtifact.audit_id == audit.id, ExportArtifact.fmt == fmt
            )
        )
        or 0
    ) + 1
    directory: Path = get_settings().export_path / audit.audit_code
    directory.mkdir(parents=True, exist_ok=True)
    file_name = f"{audit.audit_code}_v{version}.{fmt}"
    path = directory / file_name
    path.write_bytes(data)

    artifact = ExportArtifact(
        audit_id=audit.id,
        kind="audit_report",
        fmt=fmt,
        version=version,
        file_name=file_name,
        storage_path=str(path),
        file_size=len(data),
        sha256=sha256_bytes(data),
        created_by_id=created_by_id,
    )
    db.add(artifact)
    db.flush()
    return artifact
