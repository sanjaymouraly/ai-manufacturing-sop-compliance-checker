#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One-time (and safely re-runnable) environment setup for Linux and macOS.
#
#   ./scripts/setup.sh              # install deps, create .env, migrate, seed
#   ./scripts/setup.sh --no-seed    # skip loading the synthetic dataset
#   ./scripts/setup.sh --reset      # wipe var/ and rebuild from scratch
#
# Non-destructive by default: an existing .env is never overwritten and an
# existing database is left alone unless --reset is passed.
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SEED=1
RESET=0
SKIP_FRONTEND=0
for arg in "$@"; do
  case "$arg" in
    --no-seed)      SEED=0 ;;
    --reset)        RESET=1 ;;
    --backend-only) SKIP_FRONTEND=1 ;;
    -h|--help)      sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------------- #
# Prerequisites
# --------------------------------------------------------------------------- #
info "Checking prerequisites"

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
  done
fi
[ -n "$PYTHON" ] || die "Python 3.11+ not found. Install it and re-run, or set PYTHON=/path/to/python."

"$PYTHON" - <<'PY' || die "Python 3.11 or newer is required."
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
echo "    Python: $($PYTHON --version)"

if [ "$SKIP_FRONTEND" -eq 0 ]; then
  command -v node >/dev/null 2>&1 || die "Node.js 18+ not found. Install it, or re-run with --backend-only."
  echo "    Node:   $(node --version)"
  echo "    npm:    $(npm --version)"
fi

# --------------------------------------------------------------------------- #
# Optional reset
# --------------------------------------------------------------------------- #
if [ "$RESET" -eq 1 ]; then
  warn "--reset: removing var/ (database, stored uploads, exports, cache)"
  rm -rf "$REPO_ROOT/var"
fi

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
if [ -f .env ]; then
  info ".env already exists - leaving it untouched"
else
  info "Creating .env from .env.example"
  cp .env.example .env
  SECRET="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(48))')"
  "$PYTHON" - "$SECRET" <<'PY'
import pathlib, sys
secret = sys.argv[1]
path = pathlib.Path(".env")
text = path.read_text()
text = text.replace("SECRET_KEY=change-me-in-production", f"SECRET_KEY={secret}")
path.write_text(text)
PY
  echo "    Generated a unique SECRET_KEY."
fi

# --------------------------------------------------------------------------- #
# Python environment
# --------------------------------------------------------------------------- #
VENV="$REPO_ROOT/.venv"
if [ ! -d "$VENV" ]; then
  info "Creating virtual environment at .venv"
  "$PYTHON" -m venv "$VENV"
fi
# shellcheck disable=SC1091
VPY="$VENV/bin/python"

info "Installing backend dependencies"
"$VPY" -m pip install --quiet --upgrade pip
"$VPY" -m pip install --quiet -r backend/requirements-dev.txt

# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
info "Applying database migrations"
"$VPY" scripts/migrate.py

if [ "$SEED" -eq 1 ]; then
  info "Generating the synthetic dataset"
  "$VPY" scripts/generate_dataset.py
  info "Seeding the database (demo users, SOPs, reports, conflicts)"
  if [ "$RESET" -eq 1 ]; then
    "$VPY" scripts/seed.py --reset
  else
    "$VPY" scripts/seed.py
  fi
else
  info "Skipping dataset seed (--no-seed)"
fi

# --------------------------------------------------------------------------- #
# Frontend
# --------------------------------------------------------------------------- #
if [ "$SKIP_FRONTEND" -eq 0 ]; then
  info "Installing frontend dependencies"
  (cd frontend && if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi)
fi

cat <<'DONE'

------------------------------------------------------------------
Setup complete.

  Start everything:   ./scripts/start.sh
  Backend only:       ./scripts/start.sh --backend-only
  Run the tests:      .venv/bin/python -m pytest backend/tests -q
  Run the benchmark:  cd backend && ../.venv/bin/python -m eval.harness

Demo accounts (synthetic data - change before any real use):
  admin    / Admin#12345      full access, AI provider settings
  auditor  / Auditor#12345    uploads and runs analyses
  reviewer / Reviewer#12345   signs off findings and conflicts

This tool is decision support only. It does not certify compliance.
------------------------------------------------------------------
DONE
