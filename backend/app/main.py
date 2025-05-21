"""FastAPI application entrypoint."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.deps import rate_limit
from app.logging_setup import configure_logging, get_logger
from app.models.entities import User
from app.models.enums import Role
from app.security import hash_password

logger = get_logger(__name__)


def bootstrap_database() -> None:
    """Create tables (idempotent) and ensure an initial administrator exists."""
    settings = get_settings()
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        existing = db.scalar(select(User).limit(1))
        if existing is None:
            admin = User(
                username=settings.bootstrap_admin_username,
                full_name="Initial administrator",
                role=Role.ADMIN,
                password_hash=hash_password(settings.bootstrap_admin_password),
            )
            db.add(admin)
            db.commit()
            logger.warning(
                "Created initial administrator '%s'. Change this password immediately.",
                settings.bootstrap_admin_username,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):  # pragma: no cover - startup/shutdown wiring
    configure_logging()
    settings = get_settings()
    logger.info("Starting %s v%s (%s)", settings.app_name, settings.app_version, settings.environment)
    bootstrap_database()
    logger.info("Database ready at %s", settings.sqlalchemy_url)
    yield
    logger.info("Shutting down")


def create_app() -> FastAPI:
    # Configure logging here as well as in lifespan: warnings raised while the
    # app is being assembled (e.g. a missing frontend build) must be visible.
    configure_logging()
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Evidence-first, AI-assisted compliance checking of manufacturing process execution "
            "records against approved SOP revisions. Decision support only — not a replacement for "
            "qualified quality personnel or an approved quality management system."
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list or ["*"],
        allow_origin_regex=r"https://.*\.e2b\.app|http://localhost:\d+|http://127\.0\.0\.1:\d+",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled error", extra={"request_id": request_id, "path": request.url.path})
            raise
        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-ms"] = str(duration_ms)
        if request.url.path.startswith("/api") and not request.url.path.endswith(
            ("/health", "/openapi.json")
        ):
            logger.info(
                "%s %s -> %s (%dms)",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                extra={
                    "request_id": request_id,
                    "path": request.url.path,
                    "method": request.method,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                    "actor": getattr(request.state, "actor", None),
                },
            )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": "Request validation failed.",
                "errors": [
                    {"field": ".".join(str(p) for p in err.get("loc", [])), "message": err.get("msg")}
                    for err in exc.errors()
                ],
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        logger.warning("ValueError on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc), "request_id": getattr(request.state, "request_id", None)},
        )

    from fastapi import Depends

    from app.api.routes import audits, auth, conflicts, documents, providers, reports, system

    api = APIRouter(prefix="/api", dependencies=[Depends(rate_limit)])
    api.include_router(system.router)
    api.include_router(auth.router)
    api.include_router(auth.users_router)
    api.include_router(documents.router)
    api.include_router(reports.router)
    api.include_router(audits.router)
    api.include_router(audits.findings_router)
    api.include_router(conflicts.router)
    api.include_router(providers.router)
    api.include_router(system.analytics_router)
    api.include_router(system.exports_router)
    api.include_router(system.events_router)
    api.include_router(system.settings_router)
    app.include_router(api)

    def api_index() -> dict[str, Any]:
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/api/docs",
            "health": "/api/health",
            "notice": "AI-assisted decision support. Not a certification system.",
        }

    dist = settings.frontend_dist_path
    if dist is None:
        if settings.serve_frontend:
            logger.warning(
                "SERVE_FRONTEND is on but no built frontend was found "
                "(looked for index.html under %s). Run 'npm run build' in frontend/. "
                "Serving the API only.",
                settings.frontend_dist or "frontend/dist",
            )

        @app.get("/", include_in_schema=False)
        def root() -> dict[str, Any]:
            return api_index()

    else:
        # Single-port deployment: hashed build assets get a long cache, index.html
        # never does, and any unknown non-/api path falls through to the SPA so
        # client-side routes survive a hard refresh.
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        logger.info("Serving the built frontend from %s", dist)
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/api", include_in_schema=False)
        def api_root() -> dict[str, Any]:
            return api_index()

        _INDEX = dist / "index.html"
        _NO_STORE = {"Cache-Control": "no-store, max-age=0"}

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> Any:
            if full_path.startswith("api/"):
                return JSONResponse(status_code=404, content={"detail": "Not found."})
            candidate = (dist / full_path).resolve()
            # Only ever serve files from inside the build directory.
            if full_path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
                return FileResponse(candidate)
            return FileResponse(_INDEX, headers=_NO_STORE)

    return app


app = create_app()
