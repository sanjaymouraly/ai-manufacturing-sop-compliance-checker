#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Start the API and the web UI together (Linux/macOS).
#
#   ./scripts/start.sh                 # API on :8000, UI on :5173
#   ./scripts/start.sh --backend-only  # API only
#   ./scripts/start.sh --prod          # build the UI and serve it from the API
#   API_PORT=9000 WEB_PORT=3000 ./scripts/start.sh
#
# Ctrl-C stops both processes.
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"
API_HOST="${API_HOST:-0.0.0.0}"
BACKEND_ONLY=0
PROD=0

for arg in "$@"; do
  case "$arg" in
    --backend-only) BACKEND_ONLY=1 ;;
    --prod)         PROD=1 ;;
    -h|--help)      sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

VPY="$REPO_ROOT/.venv/bin/python"
[ -x "$VPY" ] || die "No virtual environment found. Run ./scripts/setup.sh first."

PIDS=()
cleanup() {
  info "Shutting down"
  for pid in "${PIDS[@]:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [ "$PROD" -eq 1 ] && [ "$BACKEND_ONLY" -eq 0 ]; then
  info "Building the frontend for production"
  (cd frontend && npm run build)
  export SERVE_FRONTEND=1
  export FRONTEND_DIST="$REPO_ROOT/frontend/dist"
fi

info "Starting the API on http://localhost:${API_PORT}  (docs at /api/docs)"
(cd backend && exec "$VPY" -m uvicorn app.main:app --host "$API_HOST" --port "$API_PORT") &
PIDS+=($!)

# Wait for the API to answer before bringing up the UI, so the first page load
# never races the backend.
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${API_PORT}/api/health" >/dev/null 2>&1; then break; fi
  sleep 0.5
done

if [ "$BACKEND_ONLY" -eq 1 ]; then
  info "Backend only. Press Ctrl-C to stop."
elif [ "$PROD" -eq 1 ]; then
  info "Production mode: the API is serving the built UI at http://localhost:${API_PORT}"
else
  [ -d frontend/node_modules ] || die "frontend/node_modules missing. Run ./scripts/setup.sh first."
  info "Starting the web UI on http://localhost:${WEB_PORT}"
  export API_PROXY_TARGET="http://127.0.0.1:${API_PORT}"
  export WEB_PORT
  (cd frontend && exec npm run dev -- --host 0.0.0.0 --port "$WEB_PORT" --strictPort) &
  PIDS+=($!)
fi

cat <<DONE

------------------------------------------------------------------
  API      http://localhost:${API_PORT}/api/health
  API docs http://localhost:${API_PORT}/api/docs
$([ "$BACKEND_ONLY" -eq 0 ] && [ "$PROD" -eq 0 ] && echo "  Web UI   http://localhost:${WEB_PORT}")
$([ "$PROD" -eq 1 ] && echo "  Web UI   http://localhost:${API_PORT}")

  Sign in with  auditor / Auditor#12345  (synthetic demo data)

  Decision support only - this tool does not certify compliance.
------------------------------------------------------------------
DONE

wait
