# ---------------------------------------------------------------------------
# AI Manufacturing SOP Compliance Checker
#
# Single-port image: the FastAPI backend serves the built React SPA, so the
# whole application runs as one container on one port.
#
#   docker build -t sop-auditor .
#   docker run --rm -p 8000:8000 -v sop-data:/data sop-auditor
#   open http://localhost:8000
# ---------------------------------------------------------------------------

# --- Stage 1: build the frontend -------------------------------------------
FROM node:20-bookworm-slim AS web

WORKDIR /build

# Copy manifests first so dependency installation is cached independently of
# source changes.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# --- Stage 2: runtime -------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is used by the container healthcheck below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY data/ ./data/
COPY --from=web /build/dist ./frontend/dist

# Writable state (database, uploads, exports, caches) lives on a volume so it
# survives container replacement.
ENV VAR_DIR=/data \
    SERVE_FRONTEND=true \
    FRONTEND_DIST=/app/frontend/dist \
    ENVIRONMENT=production \
    PYTHONPATH=/app/backend

RUN mkdir -p /data \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app /data
USER appuser

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# docker-entrypoint.sh migrates the database (and optionally seeds it) before
# handing control to uvicorn.
COPY --chown=appuser:appuser docker-entrypoint.sh /app/docker-entrypoint.sh
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/backend"]
