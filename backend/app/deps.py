"""Shared FastAPI dependencies: auth, RBAC, pagination, rate limiting."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models.entities import ApiAuditEvent, User
from app.models.enums import Role
from app.security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> User:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired; sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid authentication token.") from exc

    user = db.get(User, int(payload.get("uid", 0)))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User account is inactive or missing.")
    request.state.actor = user.username
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


class RequireRole:
    """Role-based access control dependency."""

    def __init__(self, *roles: str):
        self.roles = set(roles)

    def __call__(self, user: CurrentUser) -> User:
        if user.role == Role.ADMIN:
            return user  # administrators have full access
        if user.role not in self.roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Role '{user.role}' is not permitted to perform this action "
                f"(requires one of: {', '.join(sorted(self.roles))}).",
            )
        return user


require_admin = RequireRole(Role.ADMIN)
require_auditor = RequireRole(Role.ADMIN, Role.AUDITOR)
# Separation of duties: the auditor who runs an analysis must not also sign off the
# review of its findings. Only reviewers (and administrators) can take review
# decisions or resolve SOP conflicts.
require_reviewer = RequireRole(Role.ADMIN, Role.REVIEWER)
require_any = RequireRole(Role.ADMIN, Role.AUDITOR, Role.REVIEWER, Role.VIEWER)


class PaginationParams:
    def __init__(self, page: int = 1, page_size: int = 25):
        self.page = max(1, page)
        self.page_size = min(200, max(1, page_size))

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


Pagination = Annotated[PaginationParams, Depends()]


class RateLimiter:
    """In-process sliding-window limiter (sufficient for a single-node install)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window_seconds: int = 60) -> None:
        now = time.time()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Rate limit exceeded ({limit} requests per {window_seconds}s). Please slow down.",
            )
        bucket.append(now)

    def reset(self) -> None:
        self._hits.clear()


rate_limiter = RateLimiter()


def rate_limit(request: Request) -> None:
    settings = get_settings()
    if settings.rate_limit_per_minute <= 0:
        return
    client = request.client.host if request.client else "unknown"
    rate_limiter.check(f"{client}:{request.url.path}", settings.rate_limit_per_minute)


def record_event(
    db: Session,
    *,
    event_type: str,
    actor: User | None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    message: str = "",
    payload: dict | None = None,
) -> None:
    db.add(
        ApiAuditEvent(
            event_type=event_type,
            actor_id=actor.id if actor else None,
            actor_name=actor.username if actor else None,
            entity_type=entity_type,
            entity_id=entity_id,
            message=message,
            payload=payload or {},
        )
    )
