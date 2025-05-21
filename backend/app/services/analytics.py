"""Analytics aggregations for the dashboard.

All numbers are counts of *records produced by this system*, clearly labelled as
AI-assisted analysis output — not validated manufacturing performance metrics.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.entities import (
    Audit,
    AuditFinding,
    Citation,
    ProcessReport,
    ReviewAction,
    SopConflict,
    SopDocument,
    SopRevision,
)
from app.models.enums import AuditOutcome, ConflictStatus, ReviewStatus

CAVEAT = (
    "Counts summarise AI-assisted analysis output and reviewer activity in this system. "
    "They are not validated manufacturing performance indicators."
)


def dashboard(db: Session, *, days: int = 90) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)

    documents = db.scalar(select(func.count(SopDocument.id))) or 0
    revisions = db.scalar(select(func.count(SopRevision.id))) or 0
    reports = db.scalar(select(func.count(ProcessReport.id))) or 0
    audits = db.scalar(select(func.count(Audit.id))) or 0
    findings = db.scalar(select(func.count(AuditFinding.id))) or 0

    by_outcome = dict(db.execute(select(Audit.outcome, func.count(Audit.id)).group_by(Audit.outcome)).all())
    by_severity = dict(
        db.execute(
            select(AuditFinding.severity, func.count(AuditFinding.id)).group_by(AuditFinding.severity)
        ).all()
    )
    by_category = dict(
        db.execute(
            select(AuditFinding.category, func.count(AuditFinding.id)).group_by(AuditFinding.category)
        ).all()
    )
    by_classification = dict(
        db.execute(
            select(AuditFinding.classification, func.count(AuditFinding.id)).group_by(
                AuditFinding.classification
            )
        ).all()
    )
    by_review = dict(
        db.execute(
            select(AuditFinding.review_status, func.count(AuditFinding.id)).group_by(
                AuditFinding.review_status
            )
        ).all()
    )
    conflicts_by_status = dict(
        db.execute(select(SopConflict.status, func.count(SopConflict.id)).group_by(SopConflict.status)).all()
    )
    conflicts_by_type = dict(
        db.execute(
            select(SopConflict.conflict_type, func.count(SopConflict.id)).group_by(SopConflict.conflict_type)
        ).all()
    )

    citations_total = db.scalar(select(func.count(Citation.id))) or 0
    citations_verified = db.scalar(select(func.count(Citation.id)).where(Citation.verified.is_(True))) or 0

    missing_step_rows = db.execute(
        select(AuditFinding.requirement_key, func.count(AuditFinding.id).label("n"))
        .where(AuditFinding.category == "missing_step", AuditFinding.requirement_key.is_not(None))
        .group_by(AuditFinding.requirement_key)
        .order_by(func.count(AuditFinding.id).desc())
        .limit(10)
    ).all()

    recurring_conflicts = db.execute(
        select(SopConflict.title, SopConflict.conflict_type, func.count(SopConflict.id).label("n"))
        .group_by(SopConflict.conflict_type, SopConflict.title)
        .order_by(func.count(SopConflict.id).desc())
        .limit(8)
    ).all()

    trend_rows = db.execute(
        select(
            func.date(Audit.created_at).label("day"),
            func.count(Audit.id),
            func.sum(case((Audit.outcome == AuditOutcome.DEVIATIONS_FOUND, 1), else_=0)),
            func.sum(case((Audit.requires_human_review.is_(True), 1), else_=0)),
        )
        .where(Audit.created_at >= since)
        .group_by(func.date(Audit.created_at))
        .order_by(func.date(Audit.created_at))
    ).all()

    reviewed = db.execute(
        select(Audit.created_at, Audit.reviewed_at).where(Audit.reviewed_at.is_not(None))
    ).all()
    turnarounds = [
        (r[1] - r[0]).total_seconds() / 3600.0
        for r in reviewed
        if r[0] and r[1] and (r[1] - r[0]).total_seconds() >= 0
    ]
    avg_turnaround = round(sum(turnarounds) / len(turnarounds), 2) if turnarounds else None

    mode_rows = db.execute(
        select(Audit.mode, func.count(Audit.id), func.avg(Audit.duration_ms)).group_by(Audit.mode)
    ).all()

    return {
        "caveat": CAVEAT,
        "totals": {
            "sop_documents": documents,
            "sop_revisions": revisions,
            "process_reports": reports,
            "audits": audits,
            "findings": findings,
            "open_conflicts": conflicts_by_status.get(ConflictStatus.OPEN, 0)
            + conflicts_by_status.get(ConflictStatus.ESCALATED, 0),
            "audits_requiring_review": db.scalar(
                select(func.count(Audit.id)).where(
                    Audit.requires_human_review.is_(True),
                    Audit.review_status.in_([ReviewStatus.OPEN, ReviewStatus.IN_REVIEW]),
                )
            )
            or 0,
            "open_findings": by_review.get(ReviewStatus.OPEN, 0),
        },
        "audits_by_outcome": by_outcome,
        "findings_by_severity": by_severity,
        "findings_by_category": by_category,
        "findings_by_classification": by_classification,
        "findings_by_review_status": by_review,
        "conflicts_by_status": conflicts_by_status,
        "conflicts_by_type": conflicts_by_type,
        "evidence_verification": {
            "citations_total": citations_total,
            "citations_verified": citations_verified,
            "validity_rate": round(citations_verified / citations_total, 4) if citations_total else None,
        },
        "top_missing_steps": [{"requirement_key": r[0], "count": r[1]} for r in missing_step_rows],
        "recurring_conflicts": [{"title": r[0], "type": r[1], "count": r[2]} for r in recurring_conflicts],
        "trend": [
            {
                "date": str(r[0]),
                "audits": r[1],
                "with_deviations": int(r[2] or 0),
                "requiring_review": int(r[3] or 0),
            }
            for r in trend_rows
        ],
        "review_turnaround_hours_avg": avg_turnaround,
        "mode_performance": [
            {"mode": r[0], "audits": r[1], "avg_duration_ms": round(float(r[2] or 0), 1)} for r in mode_rows
        ],
    }


def finding_trends(db: Session, *, days: int = 180) -> list[dict[str, Any]]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.execute(
        select(
            func.date(AuditFinding.created_at),
            AuditFinding.severity,
            func.count(AuditFinding.id),
        )
        .where(AuditFinding.created_at >= since)
        .group_by(func.date(AuditFinding.created_at), AuditFinding.severity)
        .order_by(func.date(AuditFinding.created_at))
    ).all()
    buckets: dict[str, dict[str, Any]] = {}
    for day, severity, count in rows:
        bucket = buckets.setdefault(
            str(day), {"date": str(day), "critical": 0, "major": 0, "minor": 0, "info": 0}
        )
        bucket[severity] = count
    return list(buckets.values())


def reviewer_activity(db: Session, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.execute(
        select(ReviewAction.actor_name, ReviewAction.action, func.count(ReviewAction.id))
        .group_by(ReviewAction.actor_name, ReviewAction.action)
        .order_by(func.count(ReviewAction.id).desc())
        .limit(limit)
    ).all()
    return [{"actor": r[0] or "system", "action": r[1], "count": r[2]} for r in rows]
