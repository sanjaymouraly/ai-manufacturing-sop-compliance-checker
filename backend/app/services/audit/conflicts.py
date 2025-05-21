"""SOP conflict detection engine.

Detects, records and escalates situations where the *authoritative instruction
set itself* is ambiguous or contradictory. These are first-class objects
(``SopConflict``) with their own lifecycle, shown in the SOP Conflict Center.

Detected conflict types
-----------------------
* ``overlapping_effective_dates``     – two approved revisions valid at once
* ``missing_supersession``            – competing approved revisions, no chain
* ``unknown_approval_status``         – status missing/unrecognised
* ``parameter_mismatch``              – same parameter, different limits
* ``step_order_conflict``             – same steps, incompatible ordering
* ``requirement_presence_mismatch``   – mandatory step dropped without record
* ``withdrawn_reference``             – report cites superseded/archived revision
* ``cross_document_contradiction``    – two different SOPs contradict each other
* ``unresolvable_context``            – applicability cannot be decided
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging_setup import get_logger
from app.models.entities import Requirement, SopConflict, SopDocument, SopRevision
from app.models.enums import ConflictStatus, ConflictType, RevisionStatus, Severity
from app.utils.text import sha256_text, similarity, truncate
from app.utils.units import IncompatibleUnitsError, canonical_unit, convert

logger = get_logger(__name__)

PARAM_SIMILARITY_THRESHOLD = 0.55


@dataclass
class ConflictCandidate:
    conflict_type: str
    severity: str
    title: str
    description: str
    document_id: int | None = None
    revision_a_id: int | None = None
    revision_b_id: int | None = None
    requirement_a_id: int | None = None
    requirement_b_id: int | None = None
    evidence_a: dict[str, Any] = field(default_factory=dict)
    evidence_b: dict[str, Any] = field(default_factory=dict)
    applicable_context: dict[str, Any] = field(default_factory=dict)
    blocks_conclusion: bool = True

    @property
    def fingerprint(self) -> str:
        key = "|".join(
            str(x)
            for x in (
                self.conflict_type,
                self.document_id,
                sorted([self.revision_a_id or 0, self.revision_b_id or 0]),
                sorted([self.requirement_a_id or 0, self.requirement_b_id or 0]),
                self.title,
            )
        )
        return sha256_text(key)[:40]


def _evidence(
    revision: SopRevision, requirement: Requirement | None = None, note: str = ""
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "revision_id": revision.id,
        "document_label": f"{revision.document.sop_code} {revision.revision_code}"
        if revision.document
        else revision.revision_code,
        "revision_code": revision.revision_code,
        "status": revision.status,
        "effective_date": revision.effective_date.isoformat() if revision.effective_date else None,
        "expiry_date": revision.expiry_date.isoformat() if revision.expiry_date else None,
        "file_name": revision.file_name,
        "note": note,
    }
    if requirement is not None:
        payload.update(
            {
                "requirement_key": requirement.req_key,
                "section_path": requirement.section_path,
                "page": requirement.page,
                "quote": requirement.text,
                "char_start": requirement.char_start,
                "char_end": requirement.char_end,
                "parameter": requirement.parameter_name,
                "step_number": requirement.step_number,
            }
        )
    return payload


def _windows_overlap(a: SopRevision, b: SopRevision) -> bool:
    a_start, a_end = a.effective_date, a.expiry_date or date.max
    b_start, b_end = b.effective_date, b.expiry_date or date.max
    if a_start is None or b_start is None:
        return True  # unknown window: cannot rule out overlap
    return a_start <= b_end and b_start <= a_end


def _describe_constraint(req: Requirement) -> str:
    unit = f" {req.unit}" if req.unit else ""
    if req.comparator == "between":
        return f"{req.value_min:g}–{req.value_max:g}{unit}"
    if req.comparator == "==":
        tol = f" ±{req.tolerance:g}" if req.tolerance else ""
        return f"{req.value_min:g}{unit}{tol}"
    bound = req.value_min if req.value_min is not None else req.value_max
    return f"{req.comparator or '='} {bound:g}{unit}" if bound is not None else "unspecified"


def _constraints_conflict(a: Requirement, b: Requirement) -> tuple[bool, str]:
    """Return (conflict, explanation) for two parameter constraints."""
    if not a.comparator or not b.comparator:
        return False, ""
    if canonical_unit(a.unit) != canonical_unit(b.unit):
        try:
            # comparable dimensions -> convert b into a's unit for comparison
            b_min = convert(b.value_min, b.unit, a.unit) if b.value_min is not None else None
            b_max = convert(b.value_max, b.unit, a.unit) if b.value_max is not None else None
        except (IncompatibleUnitsError, TypeError):
            return True, (
                f"Incompatible units: '{a.unit or 'none'}' vs '{b.unit or 'none'}' for the same parameter."
            )
    else:
        b_min, b_max = b.value_min, b.value_max

    def _interval(req: Requirement, lo: float | None, hi: float | None) -> tuple[float, float]:
        if req.comparator == "between":
            return (lo if lo is not None else float("-inf"), hi if hi is not None else float("inf"))
        if req.comparator == "==":
            tol = req.tolerance or 0.0
            base = lo if lo is not None else 0.0
            return (base - tol, base + tol)
        if req.comparator in {">=", ">"}:
            return (lo if lo is not None else float("-inf"), float("inf"))
        if req.comparator in {"<=", "<"}:
            return (float("-inf"), hi if hi is not None else (lo if lo is not None else float("inf")))
        return (float("-inf"), float("inf"))

    a_lo, a_hi = _interval(a, a.value_min, a.value_max)
    b_lo, b_hi = _interval(b, b_min, b_max)
    overlap = max(a_lo, b_lo) <= min(a_hi, b_hi)
    if overlap:
        return False, ""
    return True, (
        f"Required {a.parameter_name or 'value'} is {_describe_constraint(a)} in one revision and "
        f"{_describe_constraint(b)} in the other; the acceptance ranges do not overlap."
    )


# --------------------------------------------------------------------------- #
# Detectors
# --------------------------------------------------------------------------- #
def detect_metadata_conflicts(document: SopDocument, revisions: list[SopRevision]) -> list[ConflictCandidate]:
    out: list[ConflictCandidate] = []
    approved = [r for r in revisions if r.status == RevisionStatus.APPROVED]

    for i, a in enumerate(approved):
        for b in approved[i + 1 :]:
            if not _windows_overlap(a, b):
                continue
            a_supersedes_b = (
                a.supersedes_revision_code or ""
            ).strip().lower() == b.revision_code.strip().lower()
            b_supersedes_a = (
                b.supersedes_revision_code or ""
            ).strip().lower() == a.revision_code.strip().lower()
            if a_supersedes_b or b_supersedes_a:
                older, newer = (b, a) if a_supersedes_b else (a, b)
                out.append(
                    ConflictCandidate(
                        conflict_type=ConflictType.MISSING_SUPERSESSION,
                        severity=Severity.MAJOR,
                        title=f"{document.sop_code}: superseded revision {older.revision_code} still marked approved",
                        description=(
                            f"Revision {newer.revision_code} declares that it supersedes {older.revision_code}, "
                            f"but {older.revision_code} is still recorded with status 'approved' and an open "
                            "validity window. Document status metadata is inconsistent."
                        ),
                        document_id=document.id,
                        revision_a_id=older.id,
                        revision_b_id=newer.id,
                        evidence_a=_evidence(older, note="Still marked approved"),
                        evidence_b=_evidence(newer, note=f"Supersedes {older.revision_code}"),
                        blocks_conclusion=False,
                    )
                )
                continue
            out.append(
                ConflictCandidate(
                    conflict_type=ConflictType.OVERLAPPING_EFFECTIVE_DATES,
                    severity=Severity.CRITICAL,
                    title=f"{document.sop_code}: revisions {a.revision_code} and {b.revision_code} are both approved and in force",
                    description=(
                        f"Revisions {a.revision_code} (effective "
                        f"{a.effective_date or 'unknown'} – {a.expiry_date or 'open'}) and "
                        f"{b.revision_code} (effective {b.effective_date or 'unknown'} – "
                        f"{b.expiry_date or 'open'}) have overlapping validity windows with no documented "
                        "supersession. The authoritative instruction set is ambiguous."
                    ),
                    document_id=document.id,
                    revision_a_id=a.id,
                    revision_b_id=b.id,
                    evidence_a=_evidence(a),
                    evidence_b=_evidence(b),
                    blocks_conclusion=True,
                )
            )

    for revision in revisions:
        if revision.status == RevisionStatus.UNKNOWN:
            out.append(
                ConflictCandidate(
                    conflict_type=ConflictType.UNKNOWN_APPROVAL_STATUS,
                    severity=Severity.MAJOR,
                    title=f"{document.sop_code} {revision.revision_code}: approval status unknown",
                    description=(
                        "The document does not state a recognised approval status, or claims approval without "
                        "a recorded approver. It cannot be treated as authoritative until clarified."
                    ),
                    document_id=document.id,
                    revision_a_id=revision.id,
                    evidence_a=_evidence(
                        revision, note="; ".join(revision.extraction_warnings or []) or "No status field"
                    ),
                    blocks_conclusion=True,
                )
            )
        if revision.superseded_by_revision_code:
            target = (revision.superseded_by_revision_code or "").strip().lower()
            if not any(r.revision_code.strip().lower() == target for r in revisions):
                out.append(
                    ConflictCandidate(
                        conflict_type=ConflictType.MISSING_SUPERSESSION,
                        severity=Severity.MAJOR,
                        title=(
                            f"{document.sop_code} {revision.revision_code}: superseding revision "
                            f"'{revision.superseded_by_revision_code}' is not in the library"
                        ),
                        description=(
                            f"Revision {revision.revision_code} states it was superseded by "
                            f"'{revision.superseded_by_revision_code}', which is not registered. The current "
                            "authoritative instruction set cannot be confirmed."
                        ),
                        document_id=document.id,
                        revision_a_id=revision.id,
                        evidence_a=_evidence(revision, note="Dangling supersession reference"),
                        blocks_conclusion=True,
                    )
                )
    return out


def detect_requirement_conflicts(
    document: SopDocument,
    revisions: list[SopRevision],
    requirements_by_revision: dict[int, list[Requirement]],
) -> list[ConflictCandidate]:
    """Compare requirement content between revisions that are simultaneously in force."""
    out: list[ConflictCandidate] = []
    approved = [r for r in revisions if r.status == RevisionStatus.APPROVED]
    for i, a in enumerate(approved):
        for b in approved[i + 1 :]:
            if not _windows_overlap(a, b):
                continue
            reqs_a = requirements_by_revision.get(a.id, [])
            reqs_b = requirements_by_revision.get(b.id, [])

            # Parameter mismatches on matched requirements
            for ra in reqs_a:
                if not ra.comparator:
                    continue
                best: tuple[Requirement, float] | None = None
                for rb in reqs_b:
                    if not rb.comparator:
                        continue
                    same_param = (
                        ra.parameter_name and rb.parameter_name and ra.parameter_name == rb.parameter_name
                    )
                    score = similarity(ra.text, rb.text)
                    if ra.step_number and ra.step_number == rb.step_number:
                        score = max(score, 0.9)
                    if not same_param and score < PARAM_SIMILARITY_THRESHOLD:
                        continue
                    if best is None or score > best[1]:
                        best = (rb, score)
                if not best:
                    continue
                rb, score = best
                conflict, explanation = _constraints_conflict(ra, rb)
                if conflict:
                    out.append(
                        ConflictCandidate(
                            conflict_type=ConflictType.PARAMETER_MISMATCH,
                            severity=Severity.CRITICAL,
                            title=(
                                f"{document.sop_code}: conflicting {ra.parameter_name or 'parameter'} limits "
                                f"between {a.revision_code} and {b.revision_code}"
                            ),
                            description=explanation,
                            document_id=document.id,
                            revision_a_id=a.id,
                            revision_b_id=b.id,
                            requirement_a_id=ra.id,
                            requirement_b_id=rb.id,
                            evidence_a=_evidence(a, ra),
                            evidence_b=_evidence(b, rb),
                            applicable_context={"a": a.applicability, "b": b.applicability},
                            blocks_conclusion=True,
                        )
                    )

            # Step-order conflicts: same two activities ordered differently
            out.extend(_detect_order_conflicts(document, a, b, reqs_a, reqs_b))

            # Mandatory requirement dropped between concurrently valid revisions
            for ra in reqs_a:
                if ra.obligation != "mandatory" or ra.comparator:
                    continue
                if not any(similarity(ra.text, rb.text) >= 0.62 for rb in reqs_b):
                    out.append(
                        ConflictCandidate(
                            conflict_type=ConflictType.REQUIREMENT_PRESENCE_MISMATCH,
                            severity=Severity.MAJOR,
                            title=(
                                f"{document.sop_code}: mandatory requirement present in {a.revision_code} "
                                f"but absent from {b.revision_code}"
                            ),
                            description=(
                                f"'{truncate(ra.text, 140)}' is mandatory in {a.revision_code} and has no "
                                f"counterpart in {b.revision_code}, while both revisions are approved and in force."
                            ),
                            document_id=document.id,
                            revision_a_id=a.id,
                            revision_b_id=b.id,
                            requirement_a_id=ra.id,
                            evidence_a=_evidence(a, ra),
                            evidence_b=_evidence(b, note="No corresponding requirement found"),
                            blocks_conclusion=True,
                        )
                    )
    return out


def _detect_order_conflicts(
    document: SopDocument,
    a: SopRevision,
    b: SopRevision,
    reqs_a: list[Requirement],
    reqs_b: list[Requirement],
) -> list[ConflictCandidate]:
    out: list[ConflictCandidate] = []
    pairs: list[tuple[Requirement, Requirement]] = []
    used: set[int] = set()
    for ra in reqs_a:
        best: tuple[Requirement, float] | None = None
        for rb in reqs_b:
            if rb.id in used:
                continue
            score = similarity(ra.text, rb.text)
            if best is None or score > best[1]:
                best = (rb, score)
        if best and best[1] >= 0.72:
            pairs.append((ra, best[0]))
            used.add(best[0].id)

    for i, (a1, b1) in enumerate(pairs):
        for a2, b2 in pairs[i + 1 :]:
            order_a = a1.seq < a2.seq
            order_b = b1.seq < b2.seq
            if order_a != order_b:
                out.append(
                    ConflictCandidate(
                        conflict_type=ConflictType.STEP_ORDER_CONFLICT,
                        severity=Severity.MAJOR,
                        title=(
                            f"{document.sop_code}: step order differs between {a.revision_code} "
                            f"and {b.revision_code}"
                        ),
                        description=(
                            f"In {a.revision_code}, '{truncate(a1.text, 90)}' precedes "
                            f"'{truncate(a2.text, 90)}'. In {b.revision_code} the order is reversed. "
                            "Both revisions are approved and in force, so the required sequence is ambiguous."
                        ),
                        document_id=document.id,
                        revision_a_id=a.id,
                        revision_b_id=b.id,
                        requirement_a_id=a1.id,
                        requirement_b_id=b1.id,
                        evidence_a=_evidence(a, a1, note=f"Sequence position {a1.seq}"),
                        evidence_b=_evidence(b, b1, note=f"Sequence position {b1.seq}"),
                        blocks_conclusion=True,
                    )
                )
                return out  # one representative order conflict per revision pair
    return out


# Tokens that carry no subject information when deciding whether two requirements from
# different documents are talking about the same physical thing.
_SUBJECT_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "into",
        "onto",
        "than",
        "then",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "is",
        "are",
        "be",
        "been",
        "being",
        "was",
        "were",
        "must",
        "shall",
        "should",
        "may",
        "can",
        "will",
        "would",
        "not",
        "no",
        "any",
        "all",
        "each",
        "every",
        "other",
        "another",
        "same",
        "such",
        "only",
        "also",
        "per",
        "use",
        "using",
        "used",
        "value",
        "values",
        "result",
        "results",
        "record",
        "recorded",
        "before",
        "after",
        "during",
        "while",
        "between",
        "within",
        "least",
        "most",
        "maximum",
        "minimum",
        "max",
        "min",
        "above",
        "below",
        "over",
        "under",
        "exceed",
        "exceeds",
        "less",
        "greater",
        "more",
        "approximately",
        "approx",
        "about",
        "target",
        "nominal",
        "tolerance",
        "limit",
        "limits",
        "range",
        "ranges",
        "reading",
        "readings",
        "measurement",
        "measurements",
        "step",
        "steps",
        "operator",
        "operators",
        "ensure",
        "verify",
        "check",
        "confirm",
        "perform",
        "complete",
    ]
)


def _subject_tokens(requirement: Requirement) -> set[str]:
    """Content words that identify what a requirement is about."""
    text = (requirement.normalized_text or requirement.text or "").lower()
    tokens = re.findall(r"[a-z][a-z\-]{2,}", text)
    drop = set(_SUBJECT_STOPWORDS)
    if requirement.action_verb:
        drop.add(requirement.action_verb.lower())
    if requirement.parameter_name:
        drop.update(requirement.parameter_name.lower().split())
    if requirement.unit:
        drop.update(re.findall(r"[a-z]+", requirement.unit.lower()))
    return {t for t in tokens if t not in drop and len(t) >= 4}


def _shared_subject(a: Requirement, b: Requirement) -> set[str]:
    """Content words common to both requirements, e.g. {"joint"} for two preheat steps."""
    return _subject_tokens(a) & _subject_tokens(b)


def detect_cross_document_conflicts(
    documents: list[SopDocument], requirements_by_revision: dict[int, list[Requirement]]
) -> list[ConflictCandidate]:
    """Contradictions between *different* approved SOPs with overlapping applicability."""
    out: list[ConflictCandidate] = []
    active: list[tuple[SopDocument, SopRevision, list[Requirement]]] = []
    for document in documents:
        for revision in document.revisions:
            if revision.status != RevisionStatus.APPROVED:
                continue
            active.append((document, revision, requirements_by_revision.get(revision.id, [])))

    for i, (doc_a, rev_a, reqs_a) in enumerate(active):
        for doc_b, rev_b, reqs_b in active[i + 1 :]:
            if doc_a.id == doc_b.id:
                continue
            if not _applicability_overlaps(rev_a, rev_b):
                continue
            for ra in reqs_a:
                if not ra.comparator or not ra.parameter_name:
                    continue
                for rb in reqs_b:
                    if rb.parameter_name != ra.parameter_name or not rb.comparator:
                        continue
                    # Two gates, either of which is enough to consider the pair comparable:
                    #   a) the requirement sentences are lexically similar, or
                    #   b) they command the same action on the same *subject*.
                    # (b) catches real contradictions worded differently, e.g. "preheat the
                    # joint to at least 120 C" vs "preheat the joint to a maximum of 100 C",
                    # which only score ~0.41 lexically. The shared-subject test is what keeps
                    # (b) from pairing unrelated measurements that happen to share a generic
                    # parameter name (a shaft end-play in mm vs a carton dimension in mm).
                    same_action = (
                        bool(ra.action_verb)
                        and ra.action_verb == rb.action_verb
                        and bool(_shared_subject(ra, rb))
                    )
                    if similarity(ra.text, rb.text) < 0.45 and not same_action:
                        continue
                    conflict, explanation = _constraints_conflict(ra, rb)
                    if conflict:
                        out.append(
                            ConflictCandidate(
                                conflict_type=ConflictType.CROSS_DOCUMENT_CONTRADICTION,
                                severity=Severity.CRITICAL,
                                title=(
                                    f"{doc_a.sop_code} {rev_a.revision_code} contradicts "
                                    f"{doc_b.sop_code} {rev_b.revision_code} on {ra.parameter_name}"
                                ),
                                description=explanation,
                                document_id=doc_a.id,
                                revision_a_id=rev_a.id,
                                revision_b_id=rev_b.id,
                                requirement_a_id=ra.id,
                                requirement_b_id=rb.id,
                                evidence_a=_evidence(rev_a, ra),
                                evidence_b=_evidence(rev_b, rb),
                                applicable_context={
                                    "a": rev_a.applicability,
                                    "b": rev_b.applicability,
                                },
                                blocks_conclusion=True,
                            )
                        )
    return out


def _applicability_overlaps(a: SopRevision, b: SopRevision) -> bool:
    app_a, app_b = a.applicability or {}, b.applicability or {}
    if not app_a or not app_b:
        return True
    for key in ("lines", "products", "sites"):
        va = {str(x).lower() for x in app_a.get(key, [])}
        vb = {str(x).lower() for x in app_b.get(key, [])}
        if va and vb and not (va & vb):
            return False
    return bool(_windows_overlap(a, b))


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def persist_conflicts(db: Session, candidates: Iterable[ConflictCandidate]) -> list[SopConflict]:
    """Insert new conflicts, de-duplicated by fingerprint. Never mutates resolutions."""
    stored: list[SopConflict] = []
    for candidate in candidates:
        fingerprint = candidate.fingerprint
        existing = db.scalar(select(SopConflict).where(SopConflict.fingerprint == fingerprint))
        if existing:
            stored.append(existing)
            continue
        count = db.scalar(select(SopConflict.id).order_by(SopConflict.id.desc()).limit(1)) or 0
        conflict = SopConflict(
            conflict_code=f"CONF-{count + 1:05d}",
            fingerprint=fingerprint,
            conflict_type=candidate.conflict_type,
            severity=candidate.severity,
            status=ConflictStatus.ESCALATED if candidate.blocks_conclusion else ConflictStatus.OPEN,
            document_id=candidate.document_id,
            revision_a_id=candidate.revision_a_id,
            revision_b_id=candidate.revision_b_id,
            requirement_a_id=candidate.requirement_a_id,
            requirement_b_id=candidate.requirement_b_id,
            title=candidate.title[:255],
            description=candidate.description,
            evidence_a=candidate.evidence_a,
            evidence_b=candidate.evidence_b,
            applicable_context=candidate.applicable_context,
            blocks_conclusion=candidate.blocks_conclusion,
        )
        db.add(conflict)
        db.flush()
        stored.append(conflict)
        logger.info("Recorded SOP conflict %s (%s)", conflict.conflict_code, conflict.conflict_type)
    return stored


def scan_document(db: Session, document: SopDocument) -> list[SopConflict]:
    """Run all intra-document detectors for one SOP and persist the results."""
    revisions = list(document.revisions)
    requirements_by_revision = {
        revision.id: list(db.scalars(select(Requirement).where(Requirement.revision_id == revision.id)))
        for revision in revisions
    }
    candidates = detect_metadata_conflicts(document, revisions)
    candidates += detect_requirement_conflicts(document, revisions, requirements_by_revision)
    return persist_conflicts(db, candidates)


def scan_library(db: Session) -> list[SopConflict]:
    """Full-library scan including cross-document contradictions."""
    documents = list(db.scalars(select(SopDocument).where(SopDocument.is_archived.is_(False))))
    requirements_by_revision: dict[int, list[Requirement]] = {}
    for document in documents:
        for revision in document.revisions:
            requirements_by_revision[revision.id] = list(
                db.scalars(select(Requirement).where(Requirement.revision_id == revision.id))
            )
    candidates: list[ConflictCandidate] = []
    for document in documents:
        candidates += detect_metadata_conflicts(document, list(document.revisions))
        candidates += detect_requirement_conflicts(
            document, list(document.revisions), requirements_by_revision
        )
    candidates += detect_cross_document_conflicts(documents, requirements_by_revision)
    return persist_conflicts(db, candidates)


def open_conflicts_for_revisions(db: Session, revision_ids: list[int]) -> list[SopConflict]:
    if not revision_ids:
        return []
    stmt = select(SopConflict).where(
        SopConflict.status.in_([ConflictStatus.OPEN, ConflictStatus.ESCALATED]),
        (SopConflict.revision_a_id.in_(revision_ids)) | (SopConflict.revision_b_id.in_(revision_ids)),
    )
    return list(db.scalars(stmt))


def resolve_conflict(
    db: Session,
    conflict: SopConflict,
    *,
    choice: str,
    rationale: str,
    actor_id: int | None,
    accept_risk: bool = False,
) -> SopConflict:
    """Record a human resolution. Original evidence is never modified."""
    if not rationale or len(rationale.strip()) < 10:
        raise ValueError("A substantive rationale (min 10 characters) is required to resolve a conflict.")
    conflict.status = ConflictStatus.ACCEPTED_RISK if accept_risk else ConflictStatus.RESOLVED
    conflict.resolution_choice = choice
    conflict.resolution_rationale = rationale.strip()
    conflict.resolved_by_id = actor_id
    conflict.resolved_at = datetime.now(UTC)
    db.flush()
    return conflict
