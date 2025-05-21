"""Version authority resolution and SOP conflict detection.

The governing rule under test: *the newest document is never automatically the
authoritative one*. When authority cannot be established the engine must block and
escalate rather than guess.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.models.enums import RevisionStatus
from app.services.versioning.resolver import resolve_version


def _revision(
    code: str,
    status: str,
    effective: date | None,
    expiry: date | None = None,
    supersedes: str | None = None,
    superseded_by: str | None = None,
    rid: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=rid or abs(hash(code)) % 10000,
        revision_code=code,
        status=status,
        effective_date=effective,
        expiry_date=expiry,
        supersedes_revision_code=supersedes,
        superseded_by_revision_code=superseded_by,
        approver="Q. Manager",
        approved_at=effective,
        created_at=effective,
        applicability={},
    )


def _report(executed: date | None, declared: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        executed_at=executed,
        declared_revision_code=declared,
        line_id=None,
        product_code=None,
        site=None,
    )


class TestVersionResolution:
    def test_single_approved_revision_in_force_is_selected(self) -> None:
        revisions = [_revision("3", RevisionStatus.APPROVED, date(2025, 3, 1))]
        result = resolve_version(revisions, _report(date(2025, 4, 17)))
        assert result.blocked is False
        assert result.resolved_revision_code == "3"

    def test_superseded_revisions_are_not_authoritative(self) -> None:
        revisions = [
            _revision("1", RevisionStatus.SUPERSEDED, date(2023, 2, 1), date(2024, 5, 31)),
            _revision("2", RevisionStatus.SUPERSEDED, date(2024, 6, 1), date(2025, 2, 28)),
            _revision("3", RevisionStatus.APPROVED, date(2025, 3, 1), supersedes="2"),
        ]
        result = resolve_version(revisions, _report(date(2025, 4, 17)))
        assert result.resolved_revision_code == "3"
        assert result.blocked is False

    def test_draft_revision_is_never_selected_even_if_newest(self) -> None:
        revisions = [
            _revision("4", RevisionStatus.APPROVED, date(2024, 9, 1)),
            _revision("5", RevisionStatus.DRAFT, date(2025, 6, 1)),
        ]
        result = resolve_version(revisions, _report(date(2025, 7, 1)))
        assert result.resolved_revision_code == "4"

    def test_two_approved_revisions_without_supersession_block(self) -> None:
        """The core safety rule: ambiguous authority must not be resolved by recency."""
        revisions = [
            _revision("A", RevisionStatus.APPROVED, date(2024, 11, 1)),
            _revision("B", RevisionStatus.APPROVED, date(2025, 1, 15)),
        ]
        result = resolve_version(revisions, _report(date(2025, 3, 10)))
        assert result.blocked is True
        assert result.resolved_revision_code is None

    def test_overlap_resolves_when_supersession_is_documented(self) -> None:
        revisions = [
            _revision(
                "A",
                RevisionStatus.SUPERSEDED,
                date(2024, 11, 1),
                date(2025, 1, 14),
                superseded_by="B",
            ),
            _revision("B", RevisionStatus.APPROVED, date(2025, 1, 15), supersedes="A"),
        ]
        result = resolve_version(revisions, _report(date(2025, 3, 10)))
        assert result.blocked is False
        assert result.resolved_revision_code == "B"

    def test_execution_before_any_effective_date_blocks(self) -> None:
        revisions = [_revision("3", RevisionStatus.APPROVED, date(2025, 3, 1))]
        result = resolve_version(revisions, _report(date(2024, 8, 1)))
        assert result.blocked is True

    def test_expired_revision_is_not_in_force(self) -> None:
        revisions = [_revision("1", RevisionStatus.APPROVED, date(2023, 1, 1), date(2024, 12, 31))]
        assert resolve_version(revisions, _report(date(2025, 3, 1))).blocked is True
        assert resolve_version(revisions, _report(date(2024, 6, 1))).blocked is False

    def test_unknown_execution_date_blocks(self) -> None:
        revisions = [_revision("3", RevisionStatus.APPROVED, date(2025, 3, 1))]
        assert resolve_version(revisions, _report(None)).blocked is True

    def test_unknown_approval_status_is_not_authoritative(self) -> None:
        revisions = [_revision("1", RevisionStatus.UNKNOWN, date(2024, 1, 1))]
        assert resolve_version(revisions, _report(date(2025, 1, 1))).blocked is True

    def test_no_revisions_at_all_blocks(self) -> None:
        assert resolve_version([], _report(date(2025, 1, 1))).blocked is True


class TestSeededConflictLibrary:
    """Conflict detection over the real synthetic library."""

    @pytest.fixture(scope="class")
    def conflicts(self, seeded_app):
        from app.db import session_scope
        from app.models.entities import SopConflict

        with session_scope() as db:
            return [
                (c.conflict_code, str(c.conflict_type), str(c.severity), c.blocks_conclusion)
                for c in db.query(SopConflict).all()
            ]

    def test_overlapping_approved_revisions_are_detected(self, conflicts) -> None:
        types = [c[1] for c in conflicts]
        assert types.count("overlapping_effective_dates") == 2  # PKG-220 and BAT-440

    def test_contradictory_parameters_are_detected(self, conflicts) -> None:
        assert any(c[1] == "parameter_mismatch" for c in conflicts)

    def test_cross_document_contradiction_is_detected(self, conflicts) -> None:
        cross = [c for c in conflicts if c[1] == "cross_document_contradiction"]
        assert len(cross) == 1, f"expected exactly one cross-document conflict, got {cross}"

    def test_unknown_approval_status_is_flagged(self, conflicts) -> None:
        assert any(c[1] == "unknown_approval_status" for c in conflicts)

    def test_material_conflicts_block_conclusions(self, conflicts) -> None:
        criticals = [c for c in conflicts if c[2] == "critical"]
        assert criticals
        assert all(c[3] is True for c in criticals)

    def test_no_spurious_conflicts(self, conflicts) -> None:
        """Unrelated measurements sharing a generic parameter must not collide."""
        assert len(conflicts) == 7, [c[0:2] for c in conflicts]
