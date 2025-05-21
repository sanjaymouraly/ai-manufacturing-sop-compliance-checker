#!/usr/bin/env python3
"""Fail if the Alembic migrations no longer match the ORM models.

A migration that has drifted from the models is one of the nastiest bugs to
find in production: everything works on a developer machine (where the schema
was created directly from the models) and breaks on a freshly migrated
deployment. This check builds both schemas in throwaway SQLite databases and
compares them object by object.

    python scripts/check_migrations.py

Exit code 0 means they agree.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))


def _schema(db_path: Path) -> dict[tuple[str, str], str]:
    """Normalised DDL for every object in the database, keyed by (type, name)."""
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
        ).fetchall()
    finally:
        con.close()
    return {(t, n): _normalise(sql or "") for t, n, sql in rows}


def _normalise(sql: str) -> str:
    """Collapse whitespace, quoting and clause order so only real differences show.

    SQLAlchemy and Alembic emit the same constraints in a different order, which
    is not a schema difference.
    """
    sql = re.sub(r"\s+", " ", sql).replace('"', "").strip().lower()
    match = re.match(r"(create table \S+ \()(.*)(\))$", sql)
    if match:
        head, body, tail = match.groups()
        parts = sorted(p.strip() for p in _split_top_level(body))
        return head + ", ".join(parts) + tail
    return sql


def _split_top_level(body: str) -> list[str]:
    """Split a column list on commas that are not inside parentheses."""
    parts, depth, current = [], 0, []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def main() -> int:
    from alembic.config import Config
    from sqlalchemy import create_engine

    from alembic import command

    with tempfile.TemporaryDirectory(prefix="sop-migrations-") as tmp:
        tmp_path = Path(tmp)
        migrated_db = tmp_path / "migrated.db"
        orm_db = tmp_path / "orm.db"

        # 1. Schema produced by running every migration.
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "alembic"))
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{migrated_db.as_posix()}")
        command.upgrade(cfg, "head")

        # 2. Schema produced directly from the ORM models.
        from app import models  # noqa: F401  (registers the tables)
        from app.db import Base

        engine = create_engine(f"sqlite:///{orm_db.as_posix()}")
        Base.metadata.create_all(engine)
        engine.dispose()

        migrated = _schema(migrated_db)
        orm = _schema(orm_db)

    only_migrated = sorted(set(migrated) - set(orm))
    only_orm = sorted(set(orm) - set(migrated))
    differing = sorted(k for k in set(migrated) & set(orm) if migrated[k] != orm[k])

    tables = sum(1 for kind, _ in orm if kind == "table")
    print(f"ORM objects: {len(orm)} ({tables} tables)   migrated objects: {len(migrated)}")

    if not (only_migrated or only_orm or differing):
        print("OK: the migrations reproduce the ORM schema exactly.")
        return 0

    print("\nMIGRATION DRIFT DETECTED\n")
    for kind, name in only_orm:
        print(f"  missing from migrations : {kind} {name}")
    for kind, name in only_migrated:
        print(f"  not in the ORM models   : {kind} {name}")
    for key in differing:
        print(f"  different definition    : {key[0]} {key[1]}")
        print(f"      migrated: {migrated[key]}")
        print(f"      orm     : {orm[key]}")
    print(
        "\nGenerate a migration for the change:\n"
        "  cd backend && python -m alembic revision --autogenerate -m 'describe the change'"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
