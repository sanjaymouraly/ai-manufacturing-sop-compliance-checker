"""SOP version authority resolution.

Core safety rule
----------------
**The newest document is never automatically authoritative.** Authority is
derived from explicit approval status, applicability, the effective-date window
covering the execution date, and documented supersession. When authority cannot
be established the resolver returns ``blocked=True`` and the audit is forced
into ``blocked_unresolved_authority`` / human review instead of guessing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from app.models.entities import ProcessReport, SopRevision
from app.models.enums import RevisionStatus


@dataclass
class CandidateTrace:
    revision_id: int
    revision_code: str
    status: str
    effective_date: str | None
    expiry_date: str | None
    applicable: bool
    in_force_at_execution: bool | None
    authoritative: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VersionResolution:
    resolved_revision_id: int | None = None
    resolved_revision_code: str | None = None
    authority_basis: str = "undetermined"
    blocked: bool = False
    requires_human_review: bool = False
    declared_revision_code: str | None = None
    declared_revision_id: int | None = None
    declared_matches_resolved: bool | None = None
    candidates: list[CandidateTrace] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved_revision_id": self.resolved_revision_id,
            "resolved_revision_code": self.resolved_revision_code,
            "authority_basis": self.authority_basis,
            "blocked": self.blocked,
            "requires_human_review": self.requires_human_review,
            "declared_revision_code": self.declared_revision_code,
            "declared_revision_id": self.declared_revision_id,
            "declared_matches_resolved": self.declared_matches_resolved,
            "candidates": [c.to_dict() for c in self.candidates],
            "notes": self.notes,
            "conflicts": self.conflicts,
        }


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def applicability_matches(revision: SopRevision, report: ProcessReport) -> tuple[bool, list[str]]:
    """Check SOP applicability against the report context.

    An empty applicability block means "applies generally". A stated block must
    match the corresponding report attribute when that attribute is known.
    """
    reasons: list[str] = []
    applicability = revision.applicability or {}
    if not applicability:
        return True, ["SOP states no applicability restriction (applies generally)."]

    checks = [
        ("lines", report.line_id, "line"),
        ("products", report.product_code, "product"),
        ("sites", report.site, "site"),
    ]
    applicable = True
    for key, report_value, label in checks:
        allowed = [str(v) for v in applicability.get(key, [])]
        if not allowed:
            continue
        allowed_norm = {_norm(a) for a in allowed}
        if "all" in allowed_norm or "any" in allowed_norm:
            continue
        if not report_value:
            reasons.append(
                f"SOP restricts {label} to {', '.join(allowed)} but the report does not state a {label}; "
                "applicability is undetermined."
            )
            continue
        if _norm(report_value) in allowed_norm:
            reasons.append(f"{label.capitalize()} '{report_value}' matches SOP applicability.")
        else:
            applicable = False
            reasons.append(f"{label.capitalize()} '{report_value}' is not in SOP applicability {allowed}.")
    return applicable, reasons


def _in_force(revision: SopRevision, execution_date: date | None) -> bool | None:
    if execution_date is None or revision.effective_date is None:
        return None
    if revision.effective_date > execution_date:
        return False
    return not (revision.expiry_date and revision.expiry_date < execution_date)


def resolve_version(
    revisions: list[SopRevision],
    report: ProcessReport,
) -> VersionResolution:
    """Determine which SOP revision authoritatively governs ``report``."""
    resolution = VersionResolution(declared_revision_code=report.declared_revision_code)
    execution_date = report.executed_at

    if not revisions:
        resolution.blocked = True
        resolution.requires_human_review = True
        resolution.notes.append("No SOP revisions are registered for the declared procedure.")
        return resolution

    if execution_date is None:
        resolution.notes.append(
            "Report does not state an execution date; the effective-date window cannot be evaluated."
        )

    declared = None
    if report.declared_revision_code:
        declared = next(
            (r for r in revisions if _norm(r.revision_code) == _norm(report.declared_revision_code)), None
        )
        if declared is None:
            resolution.notes.append(
                f"Report cites revision '{report.declared_revision_code}', which is not registered in the library."
            )
        else:
            resolution.declared_revision_id = declared.id

    traces: list[CandidateTrace] = []
    for revision in revisions:
        applicable, app_reasons = applicability_matches(revision, report)
        in_force = _in_force(revision, execution_date)
        reasons = list(app_reasons)
        authoritative = True

        if revision.status != RevisionStatus.APPROVED:
            authoritative = False
            reasons.append(f"Status '{revision.status}' is not an approved state.")
        if not applicable:
            authoritative = False
        if in_force is False:
            authoritative = False
            if revision.effective_date and execution_date and revision.effective_date > execution_date:
                reasons.append(
                    f"Effective from {revision.effective_date.isoformat()}, after execution on "
                    f"{execution_date.isoformat()}."
                )
            elif revision.expiry_date and execution_date and revision.expiry_date < execution_date:
                reasons.append(
                    f"Expired on {revision.expiry_date.isoformat()}, before execution on "
                    f"{execution_date.isoformat()}."
                )
        elif in_force is None:
            authoritative = False
            reasons.append("Effective-date window could not be evaluated (missing dates).")
        else:
            reasons.append("In force on the execution date.")

        traces.append(
            CandidateTrace(
                revision_id=revision.id,
                revision_code=revision.revision_code,
                status=revision.status,
                effective_date=revision.effective_date.isoformat() if revision.effective_date else None,
                expiry_date=revision.expiry_date.isoformat() if revision.expiry_date else None,
                applicable=applicable,
                in_force_at_execution=in_force,
                authoritative=authoritative,
                reasons=reasons,
            )
        )
    resolution.candidates = traces

    authoritative_ids = [t.revision_id for t in traces if t.authoritative]
    by_id = {r.id: r for r in revisions}

    if len(authoritative_ids) == 1:
        chosen = by_id[authoritative_ids[0]]
        resolution.resolved_revision_id = chosen.id
        resolution.resolved_revision_code = chosen.revision_code
        resolution.authority_basis = "approved_and_in_force_at_execution_date"
        resolution.notes.append(
            f"Revision {chosen.revision_code} is the only approved revision in force on the execution date."
        )
    elif len(authoritative_ids) > 1:
        chosen = _resolve_by_supersession([by_id[i] for i in authoritative_ids], resolution)  # type: ignore[assignment]
        if chosen is not None:
            resolution.resolved_revision_id = chosen.id
            resolution.resolved_revision_code = chosen.revision_code
            resolution.authority_basis = "explicit_documented_supersession"
        else:
            resolution.blocked = True
            resolution.requires_human_review = True
            resolution.authority_basis = "ambiguous_multiple_approved"
            codes = [by_id[i].revision_code for i in authoritative_ids]
            resolution.notes.append(
                "Multiple approved revisions are simultaneously in force "
                f"({', '.join(codes)}) and supersession metadata does not resolve precedence. "
                "Definitive compliance conclusions are blocked pending human review."
            )
            resolution.conflicts.append(
                {
                    "type": "overlapping_effective_dates",
                    "revision_ids": authoritative_ids,
                    "revision_codes": codes,
                    "detail": "Overlapping validity windows among approved revisions.",
                }
            )
    else:
        approved = [r for r in revisions if r.status == RevisionStatus.APPROVED]
        resolution.requires_human_review = True
        resolution.blocked = True
        if not approved:
            statuses = sorted({r.status for r in revisions})
            resolution.authority_basis = "no_approved_revision"
            resolution.notes.append(
                f"No approved revision exists for this procedure (statuses present: {', '.join(statuses)})."
            )
        elif execution_date is None:
            resolution.authority_basis = "execution_date_unknown"
            resolution.notes.append(
                "Approved revisions exist but the execution date is unknown, so the governing revision "
                "cannot be established."
            )
        else:
            applicable_approved = [t for t in traces if t.applicable and t.status == RevisionStatus.APPROVED]
            if not applicable_approved:
                resolution.authority_basis = "no_applicable_approved_revision"
                resolution.notes.append(
                    "Approved revisions exist but none apply to this line/product/site context."
                )
            else:
                resolution.authority_basis = "no_revision_in_force_at_execution_date"
                resolution.notes.append(
                    f"No approved revision was in force on {execution_date.isoformat()} "
                    "(gap or expiry in the effective-date coverage)."
                )

    if resolution.resolved_revision_id is not None and declared is not None:
        resolution.declared_matches_resolved = declared.id == resolution.resolved_revision_id
        if not resolution.declared_matches_resolved:
            resolution.notes.append(
                f"Report cites revision {declared.revision_code} ({declared.status}) but revision "
                f"{resolution.resolved_revision_code} governs this execution."
            )
    elif resolution.resolved_revision_id is not None and report.declared_revision_code:
        resolution.declared_matches_resolved = False

    return resolution


def _resolve_by_supersession(
    candidates: list[SopRevision], resolution: VersionResolution
) -> SopRevision | None:
    """Pick a winner only when supersession metadata is explicit and consistent."""
    {_norm(r.revision_code): r for r in candidates}
    not_superseded = []
    for revision in candidates:
        superseded = False
        for other in candidates:
            if other.id == revision.id:
                continue
            if _norm(other.supersedes_revision_code) and _norm(other.supersedes_revision_code) == _norm(
                revision.revision_code
            ):
                superseded = True
            if _norm(revision.superseded_by_revision_code) and _norm(
                revision.superseded_by_revision_code
            ) == _norm(other.revision_code):
                superseded = True
        if not superseded:
            not_superseded.append(revision)

    if len(not_superseded) == 1:
        winner = not_superseded[0]
        loser_codes = [r.revision_code for r in candidates if r.id != winner.id]
        resolution.notes.append(
            f"Revision {winner.revision_code} explicitly supersedes {', '.join(loser_codes)}; "
            "precedence resolved from documented supersession metadata."
        )
        # A superseded revision that is still marked 'approved' is itself a metadata defect.
        resolution.conflicts.append(
            {
                "type": "missing_supersession",
                "revision_ids": [r.id for r in candidates if r.id != winner.id],
                "revision_codes": loser_codes,
                "detail": (
                    "Superseded revision(s) are still recorded as 'approved'; the document status should "
                    "be updated to 'superseded'."
                ),
            }
        )
        return winner

    if any(_norm(r.supersedes_revision_code) for r in candidates):
        resolution.notes.append(
            "Supersession metadata is present but inconsistent (circular or incomplete chain)."
        )
    else:
        resolution.notes.append("No supersession metadata is recorded for the competing revisions.")
    return None
