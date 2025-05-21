#!/usr/bin/env python3
"""Bring the database schema up to date, safely and repeatably.

Three situations have to be handled, because the app can create its schema
directly (``Base.metadata.create_all``) for a frictionless first run:

1. No database yet                  -> ``alembic upgrade head``
2. Database exists, already stamped -> ``alembic upgrade head`` (no-op if current)
3. Database exists but NOT stamped  -> ``alembic stamp head`` first, so Alembic
   does not try to re-create tables that are already there, then upgrade.

Usage:
    python scripts/migrate.py            # upgrade to head
    python scripts/migrate.py --check    # report status, change nothing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from alembic.config import Config  # noqa: E402
from alembic.runtime.migration import MigrationContext  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402
from sqlalchemy import create_engine, inspect  # noqa: E402

from alembic import command  # noqa: E402
from app.config import get_settings  # noqa: E402


def _config() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return cfg


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report status without changing anything")
    args = parser.parse_args()

    settings = get_settings()
    url = settings.sqlalchemy_url
    engine = create_engine(url)
    cfg = _config()
    head = ScriptDirectory.from_config(cfg).get_current_head()

    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        current = MigrationContext.configure(conn).get_current_revision()

    app_tables = tables - {"alembic_version"}
    print(f"database : {url}")
    print(f"tables   : {len(app_tables)}")
    print(f"stamped  : {current or 'no'}")
    print(f"head     : {head}")

    if args.check:
        if current == head:
            print("status   : up to date")
            return 0
        print("status   : migration needed")
        return 1

    if app_tables and current is None:
        # Schema was created directly by the app; adopt it instead of rebuilding.
        print("action   : stamping the existing schema as the initial revision")
        command.stamp(cfg, "head")
    else:
        print("action   : upgrade head")
        command.upgrade(cfg, "head")

    with engine.connect() as conn:
        final = MigrationContext.configure(conn).get_current_revision()
    print(f"now at   : {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
