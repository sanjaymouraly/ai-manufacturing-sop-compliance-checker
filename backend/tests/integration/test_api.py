"""End-to-end API tests against the seeded synthetic dataset."""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from tests.conftest import auth


class TestHealthAndAuth:
    def test_health_reports_mode_without_leaking_secrets(self, seeded_app) -> None:
        body = seeded_app.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["analysis_mode"] == "deterministic"
        assert "api_key" not in json.dumps(body).lower()

    def test_login_returns_a_token(self, seeded_app) -> None:
        response = seeded_app.post("/api/auth/login", json={"username": "admin", "password": "Admin#12345"})
        assert response.status_code == 200
        assert response.json()["access_token"]

    def test_bad_password_is_rejected(self, seeded_app) -> None:
        response = seeded_app.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert response.status_code == 401

    def test_protected_route_requires_a_token(self, seeded_app) -> None:
        assert seeded_app.get("/api/documents").status_code == 401

    def test_me_returns_the_role(self, seeded_app, reviewer_token) -> None:
        body = seeded_app.get("/api/auth/me", headers=auth(reviewer_token)).json()
        assert body["role"] == "reviewer"


class TestRbac:
    def test_reviewer_cannot_upload_documents(self, seeded_app, reviewer_token) -> None:
        response = seeded_app.post(
            "/api/documents/upload",
            headers=auth(reviewer_token),
            files={"file": ("x.md", b"# SOP-TST-001 rev 1", "text/markdown")},
        )
        assert response.status_code == 403

    def test_auditor_cannot_manage_users(self, seeded_app, auditor_token) -> None:
        response = seeded_app.get("/api/users", headers=auth(auditor_token))
        assert response.status_code == 403

    def test_administrator_can_manage_users(self, seeded_app, admin_token) -> None:
        response = seeded_app.get("/api/users", headers=auth(admin_token))
        assert response.status_code == 200


class TestDocumentLibrary:
    def test_library_lists_every_seeded_sop(self, seeded_app, admin_token) -> None:
        body = seeded_app.get("/api/documents", headers=auth(admin_token), params={"page_size": 100}).json()
        codes = {item["sop_code"] for item in body["items"]}
        assert {"SOP-ASSY-101", "SOP-PKG-220", "SOP-EQP-310", "SOP-WLD-700"} <= codes

    def test_revision_detail_exposes_requirements_and_hash(self, seeded_app, admin_token) -> None:
        body = seeded_app.get(
            "/api/documents", headers=auth(admin_token), params={"search": "SOP-ASSY-101"}
        ).json()
        document_id = body["items"][0]["id"]
        detail = seeded_app.get(f"/api/documents/{document_id}", headers=auth(admin_token)).json()
        revision = next(r for r in detail["revisions"] if r["revision_code"] == "3")
        assert revision["status"] == "approved"
        assert len(revision["file_hash"]) == 64
        assert revision["requirement_count"] >= 5

    def test_duplicate_upload_is_rejected(self, seeded_app, admin_token) -> None:
        from tests.conftest import DATA_DIR

        path = DATA_DIR / "sops" / "SOP-ASSY-101_rev3.md"
        response = seeded_app.post(
            "/api/documents/upload",
            headers=auth(admin_token),
            files={"file": (path.name, path.read_bytes(), "text/markdown")},
        )
        assert response.status_code == 409

    def test_revision_diff_reports_changes(self, seeded_app, admin_token) -> None:
        body = seeded_app.get(
            "/api/documents", headers=auth(admin_token), params={"search": "SOP-ASSY-101"}
        ).json()
        detail = seeded_app.get(f"/api/documents/{body['items'][0]['id']}", headers=auth(admin_token)).json()
        rev2 = next(r for r in detail["revisions"] if r["revision_code"] == "2")["id"]
        rev3 = next(r for r in detail["revisions"] if r["revision_code"] == "3")["id"]
        diff = seeded_app.get(
            f"/api/documents/revisions/{rev2}/diff/{rev3}", headers=auth(admin_token)
        ).json()
        assert diff["left_revision_id"] == rev2
        assert diff["right_revision_id"] == rev3
        assert sum(diff["summary"].values()) > 0
        assert diff["deltas"]

    def test_unsupported_file_type_is_rejected(self, seeded_app, admin_token) -> None:
        response = seeded_app.post(
            "/api/documents/upload",
            headers=auth(admin_token),
            files={"file": ("payload.exe", b"MZ\x90\x00", "application/octet-stream")},
        )
        assert response.status_code == 415


class TestAuditWorkflow:
    @pytest.fixture(scope="class")
    def audit(self, seeded_app, auditor_token) -> dict:
        reports = seeded_app.get(
            "/api/reports", headers=auth(auditor_token), params={"search": "PR-A003", "page_size": 5}
        ).json()
        report_id = reports["items"][0]["id"]
        response = seeded_app.post("/api/audits", headers=auth(auditor_token), json={"report_id": report_id})
        assert response.status_code == 201, response.text
        return response.json()

    def test_audit_detects_the_torque_deviation(self, audit) -> None:
        categories = {f["category"] for f in audit["findings"]}
        assert "parameter_deviation" in categories

    def test_audit_resolves_the_governing_revision(self, audit) -> None:
        assert audit["version_resolution"]["resolved_revision_code"] == "3"

    def test_audit_requires_human_review(self, audit) -> None:
        assert audit["requires_human_review"] is True

    def test_every_citation_is_verified_and_resolvable(self, audit) -> None:
        citations = [c for f in audit["findings"] for c in f["citations"]]
        assert citations
        assert all(c["verified"] for c in citations)
        assert all(c["char_end"] > c["char_start"] for c in citations)

    def test_outcome_language_never_certifies(self, audit) -> None:
        blob = json.dumps(audit).lower()
        for phrase in ("certified compliant", "is compliant", "approved for release", "guarantee"):
            assert phrase not in blob

    def test_pipeline_log_records_every_stage(self, audit) -> None:
        stages = {entry["stage"] for entry in audit["pipeline_log"]}
        assert {
            "document_lookup",
            "version_resolution",
            "conflict_detection",
            "requirement_extraction",
            "evidence_retrieval",
            "compliance_comparison",
            "verification",
            "risk_classification",
            "persistence",
        } <= stages

    def test_history_is_recorded(self, seeded_app, reviewer_token, audit) -> None:
        before = seeded_app.get(f"/api/audits/{audit['id']}/history", headers=auth(reviewer_token)).json()
        seeded_app.post(
            f"/api/audits/{audit['id']}/review",
            headers=auth(reviewer_token),
            json={"action": "accept", "reason": "Deviation confirmed with production."},
        )
        after = seeded_app.get(f"/api/audits/{audit['id']}/history", headers=auth(reviewer_token)).json()
        assert len(after) == len(before) + 1
        assert after[0]["action"] == "accept"
        assert after[0]["reason"]


class TestBlockedAuthority:
    def test_overlapping_revisions_block_and_escalate(self, seeded_app, auditor_token) -> None:
        reports = seeded_app.get(
            "/api/reports", headers=auth(auditor_token), params={"search": "PR-P022"}
        ).json()
        audit = seeded_app.post(
            "/api/audits",
            headers=auth(auditor_token),
            json={"report_id": reports["items"][0]["id"]},
        ).json()
        assert audit["outcome"] == "blocked_unresolved_authority"
        assert audit["requires_human_review"] is True
        assert audit["version_resolution"]["resolved_revision_code"] is None

    def test_injection_does_not_suppress_real_findings(self, seeded_app, auditor_token) -> None:
        reports = seeded_app.get(
            "/api/reports", headers=auth(auditor_token), params={"search": "PR-X046"}
        ).json()
        audit = seeded_app.post(
            "/api/audits",
            headers=auth(auditor_token),
            json={"report_id": reports["items"][0]["id"]},
        ).json()
        categories = {f["category"] for f in audit["findings"]}
        assert "prompt_injection" in categories
        assert "parameter_deviation" in categories
        assert "missing_step" in categories


class TestReviewQueue:
    def test_findings_are_ordered_by_operational_severity(self, seeded_app, reviewer_token) -> None:
        body = seeded_app.get("/api/findings", headers=auth(reviewer_token), params={"page_size": 50}).json()
        rank = {"critical": 0, "major": 1, "minor": 2, "info": 3}
        severities = [rank[item["severity"]] for item in body["items"]]
        assert severities == sorted(severities), severities

    def test_reviewer_decision_is_appended_not_overwritten(self, seeded_app, reviewer_token) -> None:
        body = seeded_app.get("/api/findings", headers=auth(reviewer_token), params={"page_size": 1}).json()
        finding_id = body["items"][0]["id"]
        before = seeded_app.get(f"/api/findings/{finding_id}", headers=auth(reviewer_token)).json()
        response = seeded_app.post(
            f"/api/findings/{finding_id}/review",
            headers=auth(reviewer_token),
            json={"action": "accept", "reason": "Confirmed against the paper batch record."},
        )
        assert response.status_code == 200
        after = response.json()
        assert after["review_status"] == "accepted"
        # the machine's original conclusion is preserved verbatim
        assert after["classification"] == before["classification"]
        history = seeded_app.get(f"/api/findings/{finding_id}/history", headers=auth(reviewer_token)).json()
        assert any(h["reason"] for h in history)
        assert history[0]["payload"]["classification_at_review"] == before["classification"]

    def test_auditor_cannot_make_review_decisions(self, seeded_app, auditor_token) -> None:
        body = seeded_app.get("/api/findings", headers=auth(auditor_token), params={"page_size": 1}).json()
        response = seeded_app.post(
            f"/api/findings/{body['items'][0]['id']}/review",
            headers=auth(auditor_token),
            json={"action": "accept", "reason": "Not permitted for this role."},
        )
        assert response.status_code == 403


class TestConflictCenter:
    def test_conflicts_are_listed(self, seeded_app, admin_token) -> None:
        body = seeded_app.get("/api/conflicts", headers=auth(admin_token)).json()
        assert body["total"] >= 7
        assert any(i["conflict_type"] == "cross_document_contradiction" for i in body["items"])

    def test_resolution_requires_a_rationale(self, seeded_app, reviewer_token) -> None:
        body = seeded_app.get("/api/conflicts", headers=auth(reviewer_token)).json()
        conflict_id = body["items"][0]["id"]
        bad = seeded_app.post(
            f"/api/conflicts/{conflict_id}/resolve",
            headers=auth(reviewer_token),
            json={"choice": "revision_b", "rationale": ""},
        )
        assert bad.status_code in (400, 422)

    def test_resolution_is_recorded_with_rationale(self, seeded_app, reviewer_token) -> None:
        body = seeded_app.get("/api/conflicts", headers=auth(reviewer_token)).json()
        conflict = next(i for i in body["items"] if i["status"] in ("open", "escalated"))
        response = seeded_app.post(
            f"/api/conflicts/{conflict['id']}/resolve",
            headers=auth(reviewer_token),
            json={
                "choice": "revision_b",
                "rationale": "Document control confirmed revision B supersedes A as of 2025-01-15.",
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "resolved"
        assert response.json()["resolution_rationale"]


class TestExports:
    @pytest.fixture(scope="class")
    def audit_id(self, seeded_app, auditor_token) -> int:
        reports = seeded_app.get(
            "/api/reports", headers=auth(auditor_token), params={"search": "PR-A010"}
        ).json()
        return seeded_app.post(
            "/api/audits",
            headers=auth(auditor_token),
            json={"report_id": reports["items"][0]["id"]},
        ).json()["id"]

    def test_pdf_export_is_a_real_pdf(self, seeded_app, auditor_token, audit_id) -> None:
        response = seeded_app.get(f"/api/exports/audits/{audit_id}/pdf/inline", headers=auth(auditor_token))
        assert response.status_code == 200
        assert response.content[:5] == b"%PDF-"
        assert len(response.content) > 2000

    def test_json_export_carries_the_disclaimer(self, seeded_app, auditor_token, audit_id) -> None:
        response = seeded_app.get(f"/api/exports/audits/{audit_id}/json/inline", headers=auth(auditor_token))
        payload = response.json()
        assert "disclaimer" in payload
        assert "not" in payload["disclaimer"].lower()

    def test_csv_export_has_a_header_row(self, seeded_app, auditor_token, audit_id) -> None:
        response = seeded_app.get(f"/api/exports/audits/{audit_id}/csv/inline", headers=auth(auditor_token))
        assert response.status_code == 200
        first_line = response.text.splitlines()[0].lower()
        assert "finding" in first_line or "category" in first_line

    def test_exports_are_versioned_per_audit_and_format(self, seeded_app, auditor_token, audit_id) -> None:
        first = seeded_app.post(f"/api/exports/audits/{audit_id}/json", headers=auth(auditor_token)).json()
        second = seeded_app.post(f"/api/exports/audits/{audit_id}/json", headers=auth(auditor_token)).json()
        assert second["version"] == first["version"] + 1


class TestProviderSettings:
    def test_catalogue_lists_supported_providers(self, seeded_app, admin_token) -> None:
        body = seeded_app.get("/api/providers/catalogue", headers=auth(admin_token)).json()
        entries = body["providers"]
        names = {e["provider_type"] if isinstance(e, dict) else e for e in entries}
        assert {"openai", "anthropic", "openrouter", "custom", "ollama", "offline"} <= names

    def test_api_key_is_never_returned(self, seeded_app, admin_token) -> None:
        saved = seeded_app.put(
            "/api/providers",
            headers=auth(admin_token),
            json={
                "name": "primary",
                "provider_type": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "sk-test-SHOULD-NEVER-APPEAR",
                "model": "openai/gpt-4o-mini",
            },
        )
        assert saved.status_code in (200, 201), saved.text
        blob = json.dumps(saved.json())
        assert "sk-test-SHOULD-NEVER-APPEAR" not in blob
        listed = seeded_app.get("/api/providers", headers=auth(admin_token)).text
        assert "sk-test-SHOULD-NEVER-APPEAR" not in listed

    def test_paid_inference_is_refused_without_authorisation(self, seeded_app, admin_token) -> None:
        response = seeded_app.post(
            "/api/providers/test",
            headers=auth(admin_token),
            json={
                "provider_type": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "model": "openai/gpt-4o-mini",
                "api_key": "sk-test-not-used",
                "run_inference": True,
            },
        )
        assert response.status_code == 402, response.text

    def test_non_admin_cannot_read_provider_config(self, seeded_app, auditor_token) -> None:
        assert seeded_app.get("/api/providers", headers=auth(auditor_token)).status_code == 403

    def test_model_discovery_uses_the_submitted_credentials(self, seeded_app, admin_token) -> None:
        """Regression: discovery used to ignore the body and read a stored row.

        The button sits next to the model field on an unsaved form, so an
        operator entering a brand new endpoint got "a base URL is required"
        for a URL they had just typed. Worse, once any config of that type
        existed, they silently got *that* endpoint's catalogue instead.
        """
        # Offline needs no credentials and performs no network call, so it is
        # a safe way to prove the submitted body reaches the adapter.
        body = seeded_app.post(
            "/api/providers/models",
            headers=auth(admin_token),
            json={"provider_type": "offline"},
        )
        assert body.status_code == 200, body.text

        # A custom endpoint with nothing typed and nothing stored must say so
        # rather than silently resolving to some other configuration.
        missing = seeded_app.post(
            "/api/providers/models",
            headers=auth(admin_token),
            json={"provider_type": "custom"},
        )
        assert missing.status_code == 502
        assert "base URL" in missing.json()["detail"]

    def test_model_discovery_does_not_accept_a_key_in_the_query_string(self, seeded_app, admin_token) -> None:
        """API keys must never land in access logs or browser history."""
        assert seeded_app.get("/api/providers/models", headers=auth(admin_token)).status_code == 405

    def test_non_admin_cannot_discover_models(self, seeded_app, auditor_token) -> None:
        assert (
            seeded_app.post(
                "/api/providers/models",
                headers=auth(auditor_token),
                json={"provider_type": "offline"},
            ).status_code
            == 403
        )


class TestAnalytics:
    def test_dashboard_reports_totals_with_a_caveat(self, seeded_app, admin_token) -> None:
        body = seeded_app.get("/api/analytics/dashboard", headers=auth(admin_token)).json()
        assert body["totals"]["sop_documents"] >= 10
        assert body["totals"]["audits"] >= 1
        assert "caveat" in body
        assert "not" in body["caveat"].lower()

    def test_event_log_is_append_only(self, seeded_app, admin_token) -> None:
        body = seeded_app.get("/api/events", headers=auth(admin_token), params={"page_size": 20}).json()
        assert body["total"] > 0
        event_id = body["items"][0]["id"]
        assert seeded_app.delete(f"/api/events/{event_id}", headers=auth(admin_token)).status_code in (
            404,
            405,
        )


# ---------------------------------------------------------------------------
# Single-port production mode: the API also serves the built SPA.
# ---------------------------------------------------------------------------
class TestFrontendServing:
    """`scripts/start.sh --prod` sets SERVE_FRONTEND; the API must honour it."""

    @staticmethod
    def _build(tmp_path):
        dist = tmp_path / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<!doctype html><title>SOP Auditor</title>", encoding="utf-8")
        (dist / "assets" / "index-abc123.js").write_text("console.log(1);", encoding="utf-8")
        (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
        return dist

    @staticmethod
    @contextmanager
    def _client(monkeypatch, tmp_path, dist):
        """An app built with the single-port settings on, against a live schema."""
        from fastapi.testclient import TestClient

        from app.config import get_settings
        from app.db import Base, engine
        from app.main import create_app

        monkeypatch.setenv("SERVE_FRONTEND", "true")
        monkeypatch.setenv("FRONTEND_DIST", str(dist) if dist else str(tmp_path / "nonexistent"))
        get_settings.cache_clear()
        Base.metadata.create_all(bind=engine)
        try:
            with TestClient(create_app()) as client:
                yield client
        finally:
            get_settings.cache_clear()

    def test_serves_index_and_assets(self, tmp_path, monkeypatch):
        with self._client(monkeypatch, tmp_path, self._build(tmp_path)) as client:
            root = client.get("/")
            assert root.status_code == 200
            assert "SOP Auditor" in root.text
            assert client.get("/assets/index-abc123.js").status_code == 200
            assert client.get("/favicon.svg").status_code == 200

    def test_client_side_routes_survive_a_hard_refresh(self, tmp_path, monkeypatch):
        with self._client(monkeypatch, tmp_path, self._build(tmp_path)) as client:
            for path in ("/audits/12", "/sops/3/compare", "/review"):
                resp = client.get(path)
                assert resp.status_code == 200, path
                assert "SOP Auditor" in resp.text

    def test_api_routes_are_not_swallowed_by_the_spa_fallback(self, tmp_path, monkeypatch):
        with self._client(monkeypatch, tmp_path, self._build(tmp_path)) as client:
            health = client.get("/api/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"
            # An unknown /api path must 404 as JSON, never return the SPA shell.
            missing = client.get("/api/definitely-not-a-route")
            assert missing.status_code == 404
            assert "<!doctype html>" not in missing.text.lower()

    def test_path_traversal_cannot_escape_the_build_directory(self, tmp_path, monkeypatch):
        dist = self._build(tmp_path)
        secret = tmp_path / "secret.env"
        secret.write_text("LLM_API_KEY=sk-should-never-be-served", encoding="utf-8")
        with self._client(monkeypatch, tmp_path, dist) as client:
            for path in ("/../secret.env", "/..%2fsecret.env", "/%2e%2e/secret.env"):
                resp = client.get(path)
                assert "sk-should-never-be-served" not in resp.text, path

    def test_missing_build_degrades_to_api_only_instead_of_crashing(self, tmp_path, monkeypatch):
        with self._client(monkeypatch, tmp_path, None) as client:
            body = client.get("/").json()
            assert body["health"] == "/api/health"
            assert body["notice"].startswith("AI-assisted decision support")
            assert client.get("/api/health").status_code == 200


class TestHealthEndpointRobustness:
    """A health probe is only useful if it answers even when things are broken."""

    def test_reports_degraded_instead_of_500_when_the_database_is_unreachable(self, seeded_app):
        from sqlalchemy.exc import OperationalError

        import app.api.routes.system as system_module

        def boom(*_args, **_kwargs):
            raise OperationalError("SELECT 1", {}, Exception("database is locked"))

        original = system_module.get_active_config
        try:
            system_module.get_active_config = boom
            response = seeded_app.get("/api/health")
        finally:
            system_module.get_active_config = original

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        assert body["database"].startswith("error: OperationalError")
        # Still reports what it can, and never leaks the connection string.
        assert body["version"]
        assert "sqlite" not in response.text.lower()

    def test_healthy_response_never_exposes_secrets(self, seeded_app):
        body = seeded_app.get("/api/health").json()
        assert body["status"] == "ok"
        assert set(body) >= {"llm_provider", "llm_configured"}
        # A boolean, never the key itself.
        assert isinstance(body["llm_configured"], bool)
        assert "api_key" not in body
