"""Requirement-level comparison between two SOP revisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.models.entities import Requirement, SopRevision
from app.utils.text import similarity, truncate

MATCH_THRESHOLD = 0.55


@dataclass
class RequirementDelta:
    change_type: str  # added | removed | modified | unchanged | parameter_changed | reordered
    left_key: str | None = None
    right_key: str | None = None
    left_text: str | None = None
    right_text: str | None = None
    left_step: str | None = None
    right_step: str | None = None
    similarity: float = 0.0
    parameter_change: dict[str, Any] | None = None
    obligation_change: tuple[str, str] | None = None
    severity_hint: str = "info"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["obligation_change"] = list(self.obligation_change) if self.obligation_change else None
        return data


@dataclass
class RevisionDiff:
    left_revision_id: int
    right_revision_id: int
    left_label: str
    right_label: str
    deltas: list[RequirementDelta] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_revision_id": self.left_revision_id,
            "right_revision_id": self.right_revision_id,
            "left_label": self.left_label,
            "right_label": self.right_label,
            "summary": self.summary,
            "deltas": [d.to_dict() for d in self.deltas],
        }


def _param_tuple(req: Requirement) -> tuple[Any, ...]:
    return (req.parameter_name, req.comparator, req.value_min, req.value_max, req.tolerance, req.unit)


def _describe_param(req: Requirement) -> str | None:
    if not req.comparator:
        return None
    unit = f" {req.unit}" if req.unit else ""
    if req.comparator == "between":
        return f"{req.value_min:g}–{req.value_max:g}{unit}"
    if req.comparator == "==":
        tol = f" ±{req.tolerance:g}" if req.tolerance else ""
        return f"{req.value_min:g}{unit}{tol}"
    bound = req.value_min if req.value_min is not None else req.value_max
    return f"{req.comparator} {bound:g}{unit}" if bound is not None else None


def _pair_requirements(
    left: list[Requirement], right: list[Requirement]
) -> tuple[list[tuple[Requirement, Requirement, float]], list[Requirement], list[Requirement]]:
    pairs: list[tuple[Requirement, Requirement, float]] = []
    used_right: set[int] = set()

    # 1) exact step-number anchor
    right_by_step = {r.step_number: r for r in right if r.step_number}
    for lreq in left:
        if lreq.step_number and lreq.step_number in right_by_step:
            rreq = right_by_step[lreq.step_number]
            if rreq.id not in used_right:
                pairs.append((lreq, rreq, similarity(lreq.text, rreq.text)))
                used_right.add(rreq.id)

    paired_left = {p[0].id for p in pairs}
    # 2) best-similarity matching for the remainder
    for lreq in left:
        if lreq.id in paired_left:
            continue
        best: tuple[Requirement, float] | None = None
        for rreq in right:
            if rreq.id in used_right:
                continue
            score = similarity(lreq.text, rreq.text)
            if best is None or score > best[1]:
                best = (rreq, score)
        if best and best[1] >= MATCH_THRESHOLD:
            pairs.append((lreq, best[0], best[1]))
            used_right.add(best[0].id)
            paired_left.add(lreq.id)

    removed = [r for r in left if r.id not in paired_left]
    added = [r for r in right if r.id not in used_right]
    return pairs, removed, added


def diff_revisions(
    left: SopRevision, right: SopRevision, left_reqs: list[Requirement], right_reqs: list[Requirement]
) -> RevisionDiff:
    """Produce a human-readable, requirement-level diff between two revisions."""
    pairs, removed, added = _pair_requirements(left_reqs, right_reqs)
    deltas: list[RequirementDelta] = []

    for lreq, rreq, score in sorted(pairs, key=lambda p: p[0].seq):
        param_changed = _param_tuple(lreq) != _param_tuple(rreq)
        text_changed = lreq.text.strip() != rreq.text.strip()
        obligation_changed = lreq.obligation != rreq.obligation
        reordered = (lreq.step_number or "") != (rreq.step_number or "")

        if not (param_changed or text_changed or obligation_changed or reordered):
            deltas.append(
                RequirementDelta(
                    change_type="unchanged",
                    left_key=lreq.req_key,
                    right_key=rreq.req_key,
                    left_text=lreq.text,
                    right_text=rreq.text,
                    similarity=score,
                    left_step=lreq.step_number,
                    right_step=rreq.step_number,
                )
            )
            continue

        change_type = "modified"
        severity = "minor"
        notes: list[str] = []
        parameter_change = None
        if param_changed and (lreq.comparator or rreq.comparator):
            change_type = "parameter_changed"
            severity = "major"
            parameter_change = {
                "parameter": rreq.parameter_name or lreq.parameter_name,
                "before": _describe_param(lreq),
                "after": _describe_param(rreq),
                "unit_before": lreq.unit,
                "unit_after": rreq.unit,
            }
            notes.append(
                f"Requirement limit changed from {parameter_change['before'] or 'none'} "
                f"to {parameter_change['after'] or 'none'}."
            )
        if obligation_changed:
            severity = "major" if rreq.obligation in {"optional", "informative"} else severity
            notes.append(f"Obligation changed: {lreq.obligation} → {rreq.obligation}.")
        if reordered:
            if change_type == "modified" and not text_changed:
                change_type = "reordered"
            notes.append(f"Step number changed: {lreq.step_number or '—'} → {rreq.step_number or '—'}.")

        deltas.append(
            RequirementDelta(
                change_type=change_type,
                left_key=lreq.req_key,
                right_key=rreq.req_key,
                left_text=lreq.text,
                right_text=rreq.text,
                left_step=lreq.step_number,
                right_step=rreq.step_number,
                similarity=score,
                parameter_change=parameter_change,
                obligation_change=(lreq.obligation, rreq.obligation) if obligation_changed else None,
                severity_hint=severity,
                notes=notes,
            )
        )

    for req in removed:
        deltas.append(
            RequirementDelta(
                change_type="removed",
                left_key=req.req_key,
                left_text=req.text,
                left_step=req.step_number,
                severity_hint="major",
                notes=[f"Requirement present in {left.revision_code} is absent from {right.revision_code}."],
            )
        )
    for req in added:
        deltas.append(
            RequirementDelta(
                change_type="added",
                right_key=req.req_key,
                right_text=req.text,
                right_step=req.step_number,
                severity_hint="major",
                notes=[f"New requirement introduced in {right.revision_code}: {truncate(req.text, 120)}"],
            )
        )

    summary: dict[str, int] = {}
    for delta in deltas:
        summary[delta.change_type] = summary.get(delta.change_type, 0) + 1

    return RevisionDiff(
        left_revision_id=left.id,
        right_revision_id=right.id,
        left_label=f"{left.document.sop_code if left.document else ''} {left.revision_code}".strip(),
        right_label=f"{right.document.sop_code if right.document else ''} {right.revision_code}".strip(),
        deltas=deltas,
        summary=summary,
    )
