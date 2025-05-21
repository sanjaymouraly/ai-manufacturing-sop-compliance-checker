"""Evidence retrieval: link SOP requirements to observed report events.

Deterministic, explainable scoring (no embeddings required):

``score = 0.55·lexical + 0.25·step_anchor + 0.12·parameter_anchor + 0.08·verb_anchor``

An optional embedding backend can be plugged in via ``SemanticScorer`` for
installations that want it; the default keeps results reproducible offline.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Protocol

from app.models.entities import ReportEvent, Requirement
from app.utils.text import similarity, stem, token_set


class SemanticScorer(Protocol):  # pragma: no cover - interface only
    def score(self, left: str, right: str) -> float: ...


@dataclass
class EvidenceMatch:
    requirement_id: int
    event: ReportEvent | None
    score: float
    rationale: list[str] = field(default_factory=list)
    alternatives: list[tuple[int, float]] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.event is not None


def _step_anchor(requirement: Requirement, event: ReportEvent) -> float:
    if not requirement.step_number or not event.step_number:
        return 0.0
    req_step = requirement.step_number.strip()
    ev_step = str(event.step_number).strip()
    if req_step == ev_step:
        return 1.0
    if req_step.split(".")[0] == ev_step.split(".")[0]:
        return 0.35
    return 0.0


def _parameter_anchor(requirement: Requirement, event: ReportEvent) -> float:
    if not requirement.parameter_name:
        return 0.0
    if event.parameter_name and event.parameter_name.strip().lower() in {
        requirement.parameter_name.lower(),
        requirement.parameter_name.lower().replace("_", " "),
    }:
        return 1.0
    if requirement.parameter_name.replace("_", " ") in (event.text or "").lower():
        return 0.6
    return 0.0


def _verb_anchor(requirement: Requirement, event: ReportEvent) -> float:
    if not requirement.action_verb:
        return 0.0
    # Both sides must be stemmed the same way: the SOP says "attach", the operator
    # wrote "Attached".
    return 1.0 if stem(requirement.action_verb) in token_set(event.text or "") else 0.0


def score_pair(
    requirement: Requirement, event: ReportEvent, *, semantic: SemanticScorer | None = None
) -> tuple[float, list[str]]:
    lexical = similarity(requirement.text, event.text or "")
    if semantic is not None:
        # An optional semantic backend must never be able to break a deterministic audit.
        with contextlib.suppress(Exception):  # pragma: no cover - optional backend
            lexical = max(lexical, float(semantic.score(requirement.text, event.text or "")))
    step = _step_anchor(requirement, event)
    param = _parameter_anchor(requirement, event)
    verb = _verb_anchor(requirement, event)
    score = 0.55 * lexical + 0.25 * step + 0.12 * param + 0.08 * verb

    rationale: list[str] = [f"lexical similarity {lexical:.2f}"]
    if step:
        rationale.append(
            f"step number {'exact' if step == 1.0 else 'partial'} match "
            f"({requirement.step_number} ↔ {event.step_number})"
        )
    if param:
        rationale.append(f"parameter anchor '{requirement.parameter_name}'")
    if verb:
        rationale.append(f"action verb '{requirement.action_verb}'")
    return round(min(1.0, score), 4), rationale


def match_requirements(
    requirements: list[Requirement],
    events: list[ReportEvent],
    *,
    threshold: float = 0.52,
    semantic: SemanticScorer | None = None,
) -> dict[int, EvidenceMatch]:
    """Greedy, deterministic 1:1 assignment of requirements to events."""
    scored: list[tuple[float, Requirement, ReportEvent, list[str]]] = []
    for requirement in requirements:
        for event in events:
            if (event.raw_fields or {}).get("kind") == "operator_notes":
                continue
            score, rationale = score_pair(requirement, event, semantic=semantic)
            if score > 0:
                scored.append((score, requirement, event, rationale))

    scored.sort(key=lambda item: (-item[0], item[1].seq, item[2].seq))
    assigned_events: set[int] = set()
    results: dict[int, EvidenceMatch] = {
        r.id: EvidenceMatch(
            requirement_id=r.id, event=None, score=0.0, rationale=["no candidate above threshold"]
        )
        for r in requirements
    }
    alternatives: dict[int, list[tuple[int, float]]] = {r.id: [] for r in requirements}

    for score, requirement, event, rationale in scored:
        alternatives[requirement.id].append((event.id, score))
        if score < threshold:
            continue
        current = results[requirement.id]
        if current.event is not None or event.id in assigned_events:
            continue
        results[requirement.id] = EvidenceMatch(
            requirement_id=requirement.id, event=event, score=score, rationale=rationale
        )
        assigned_events.add(event.id)

    for req_id, match in results.items():
        match.alternatives = sorted(alternatives[req_id], key=lambda x: -x[1])[:3]
    return results


def find_duplicate_executions(
    requirements: list[Requirement],
    events: list[ReportEvent],
    matches: dict[int, EvidenceMatch],
    *,
    threshold: float = 0.72,
    semantic: SemanticScorer | None = None,
) -> dict[int, list[ReportEvent]]:
    """Find additional events that also satisfy an already-matched requirement."""
    duplicates: dict[int, list[ReportEvent]] = {}
    used = {m.event.id for m in matches.values() if m.event is not None}
    for requirement in requirements:
        match = matches.get(requirement.id)
        if match is None or match.event is None:
            continue
        extra: list[ReportEvent] = []
        for event in events:
            if event.id == match.event.id or (event.raw_fields or {}).get("kind") == "operator_notes":
                continue
            score, _ = score_pair(requirement, event, semantic=semantic)
            if score >= threshold:
                # another requirement already owns this event -> not a duplicate execution
                if event.id in used and event.id != match.event.id:
                    owner = next(
                        (m for m in matches.values() if m.event is not None and m.event.id == event.id), None
                    )
                    if owner is not None and owner.score >= score:
                        continue
                extra.append(event)
        if extra:
            duplicates[requirement.id] = extra
    return duplicates


def unmatched_events(events: list[ReportEvent], matches: dict[int, EvidenceMatch]) -> list[ReportEvent]:
    matched_ids = {m.event.id for m in matches.values() if m.event is not None}
    return [
        e for e in events if e.id not in matched_ids and (e.raw_fields or {}).get("kind") != "operator_notes"
    ]
