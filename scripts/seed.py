#!/usr/bin/env python3
"""Seed a database with the synthetic SOP library and process reports.

Usage
-----
    python scripts/seed.py                 # seed into the configured database
    python scripts/seed.py --reset         # drop and recreate the schema first
    python scripts/seed.py --with-audits   # also run one audit per seeded report

The script is idempotent: re-running it without ``--reset`` skips documents and
reports whose content hash is already present.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

DATA = ROOT / "data"
SOP_DIR = DATA / "synthetic" / "sops"
REPORT_DIR = DATA / "synthetic" / "reports"
SCENARIO_DIR = DATA / "scenarios"


def _load_manifest() -> dict[str, Any]:
    index = SCENARIO_DIR / "index.json"
    if not index.exists():
        raise SystemExit("Dataset not found. Run 'python scripts/generate_dataset.py' first.")
    return json.loads(index.read_text(encoding="utf-8"))


def seed(*, reset: bool = False, with_audits: bool = False, quiet: bool = False) -> dict[str, Any]:
    from sqlalchemy import select

    from app.db import Base, engine, session_scope
    from app.models.entities import ProcessReport, SopDocument, SopRevision, User
    from app.models.enums import Role
    from app.security import hash_password
    from app.services import documents as document_service
    from app.services import reports as report_service
    from app.services.audit import conflicts as conflict_engine
    from app.services.audit.pipeline import run_audit

    def say(message: str) -> None:
        if not quiet:
            print(message)

    if reset:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    manifest = _load_manifest()
    summary: dict[str, Any] = {
        "users": 0,
        "sop_revisions": 0,
        "sop_skipped": 0,
        "reports": 0,
        "reports_skipped": 0,
        "conflicts": 0,
        "audits": 0,
    }

    demo_users = [
        ("admin", "Admin#12345", Role.ADMIN, "Platform Administrator"),
        ("auditor", "Auditor#12345", Role.AUDITOR, "Dana Auditor"),
        ("reviewer", "Reviewer#12345", Role.REVIEWER, "Sam Reviewer"),
    ]

    with session_scope() as db:
        for username, password, role, full_name in demo_users:
            existing = db.query(User).filter(User.username == username).one_or_none()
            if existing is None:
                db.add(
                    User(
                        username=username,
                        full_name=full_name,
                        email=f"{username}@example.invalid",
                        role=role,
                        password_hash=hash_password(password),
                        is_active=True,
                    )
                )
                summary["users"] += 1
        db.flush()
        admin = db.query(User).filter(User.username == "admin").one()
        admin_id = admin.id

    # ---------------- SOP documents ----------------
    for entry in manifest["sop_documents"]:
        path = SOP_DIR / entry["file"]
        data = path.read_bytes()
        overrides: dict[str, Any] | None = None
        if entry.get("requires_overrides"):
            # A scanned document with no text layer cannot self-describe; the uploader
            # supplies the registration metadata manually, exactly as the UI requires.
            overrides = {
                "sop_code": entry["sop_code"],
                "revision_code": entry["revision"],
                "title": "Conveyor belt tension check",
                "status": entry["status"],
                "effective_date": entry["effective"],
            }
        with session_scope() as db:
            # Idempotency is keyed on (SOP code, revision), not only on the file
            # hash. Re-generated PDFs/DOCX files embed fresh timestamps, and text
            # files generated on another OS can differ only by line endings, so a
            # hash-only check makes a second `setup` run fail on a populated
            # database. An existing revision is never overwritten.
            existing_revision = db.scalar(
                select(SopRevision)
                .join(SopDocument, SopRevision.document_id == SopDocument.id)
                .where(
                    SopDocument.sop_code == entry["sop_code"],
                    SopRevision.revision_code == str(entry["revision"]),
                )
            )
            if existing_revision is not None:
                summary["sop_skipped"] += 1
                say(f"  = {path.name} (revision already present)")
                continue
            try:
                revision = document_service.ingest_sop_document(
                    db,
                    data=data,
                    filename=path.name,
                    uploaded_by_id=admin_id,
                    overrides=overrides,
                    run_conflict_scan=False,
                )
            except document_service.DuplicateUploadError:
                summary["sop_skipped"] += 1
                say(f"  = {path.name} (already present)")
                continue
            summary["sop_revisions"] += 1
            say(
                f"  + {path.name} -> {revision.document.sop_code} rev {revision.revision_code} "
                f"[{revision.status}] {len(revision.requirements)} requirements"
            )

    # ---------------- Library-wide conflict scan ----------------
    with session_scope() as db:
        found = conflict_engine.scan_library(db)
        summary["conflicts"] = len(found)
        for conflict in found:
            say(f"  ! {conflict.conflict_code} {conflict.conflict_type} ({conflict.severity})")

    # ---------------- Process reports ----------------
    report_ids: list[int] = []
    for scenario_entry in manifest["scenarios"]:
        path = REPORT_DIR / scenario_entry["report_file"]
        data = path.read_bytes()
        with session_scope() as db:
            # Same rationale as for SOPs: a regenerated file may hash differently
            # (line endings, PDF/DOCX metadata) while representing the same
            # record. In the seeder, filename identity is the stable key.
            existing_report = db.scalar(select(ProcessReport).where(ProcessReport.file_name == path.name))
            if existing_report is not None:
                summary["reports_skipped"] += 1
                report_ids.append(existing_report.id)
                continue
            try:
                report = report_service.ingest_process_report(
                    db, data=data, filename=path.name, uploaded_by_id=admin_id
                )
            except report_service.DuplicateReportError as exc:
                summary["reports_skipped"] += 1
                report_ids.append(exc.existing.id)
                continue
            summary["reports"] += 1
            report_ids.append(report.id)

    say(f"  + {summary['reports']} process reports ingested")

    # ---------------- Optional: run audits ----------------
    if with_audits:
        for report_id in report_ids:
            with session_scope() as db:
                report = db.get(ProcessReport, report_id)
                if report is None:
                    continue
                audit = asyncio.run(run_audit(db, report, actor_id=admin_id))
                summary["audits"] += 1
                say(
                    f"  * {audit.audit_code} {report.report_code} -> {audit.outcome} "
                    f"({len(audit.findings)} findings)"
                )

    with session_scope() as db:
        summary["total_sop_revisions"] = db.query(SopRevision).count()
        summary["total_reports"] = db.query(ProcessReport).count()

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the synthetic audit dataset.")
    parser.add_argument("--reset", action="store_true", help="drop and recreate all tables first")
    parser.add_argument("--with-audits", action="store_true", help="run an audit for every report")
    parser.add_argument("--quiet", action="store_true", help="only print the final summary")
    args = parser.parse_args()

    print("Seeding synthetic dataset ...")
    summary = seed(reset=args.reset, with_audits=args.with_audits, quiet=args.quiet)
    print("\nSummary")
    for key, value in summary.items():
        print(f"  {key:<22} {value}")
    print(
        "\nDemo credentials: admin/Admin#12345, auditor/Auditor#12345, reviewer/Reviewer#12345"
        "\nAll seeded content is synthetic and must not be used for real compliance decisions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
