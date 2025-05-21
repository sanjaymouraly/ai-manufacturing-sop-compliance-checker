"""Authentication and user management."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select

from app.config import get_settings
from app.deps import CurrentUser, DbSession, record_event, require_admin
from app.models.entities import User
from app.models.enums import Role
from app.schemas import (
    LoginRequest,
    MessageOut,
    PasswordChange,
    TokenResponse,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: DbSession) -> TokenResponse:
    from app.deps import rate_limiter

    client = request.client.host if request.client else "unknown"
    rate_limiter.check(f"login:{client}", limit=get_settings().login_rate_limit_per_minute, window_seconds=60)

    user = db.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        record_event(
            db,
            event_type="login_failed",
            actor=None,
            message=f"Failed sign-in for '{payload.username}'",
            payload={"ip": client},
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password.")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is deactivated.")

    user.last_login_at = datetime.now(UTC)
    settings = get_settings()
    token = create_access_token(user.username, role=user.role, user_id=user.id)
    record_event(db, event_type="login", actor=user, message=f"{user.username} signed in")
    return TokenResponse(
        access_token=token,
        expires_in_minutes=settings.access_token_expire_minutes,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/change-password", response_model=MessageOut)
def change_password(payload: PasswordChange, user: CurrentUser, db: DbSession) -> MessageOut:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    user.password_hash = hash_password(payload.new_password)
    record_event(db, event_type="password_changed", actor=user, message="Password changed")
    return MessageOut(message="Password updated.")


@users_router.get("", response_model=list[UserOut], dependencies=[Depends(require_admin)])
def list_users(db: DbSession) -> list[UserOut]:
    users = db.scalars(select(User).order_by(User.id)).all()
    return [UserOut.model_validate(u) for u in users]


@users_router.post("", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, db: DbSession, actor: User = Depends(require_admin)) -> UserOut:
    if payload.role not in set(Role):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown role '{payload.role}'.")
    if db.scalar(select(func.count(User.id)).where(User.username == payload.username)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists.")
    user = User(
        username=payload.username,
        email=payload.email,
        full_name=payload.full_name,
        role=payload.role,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.flush()
    record_event(
        db,
        event_type="user_created",
        actor=actor,
        entity_type="user",
        entity_id=user.id,
        message=f"Created user {user.username} ({user.role})",
    )
    return UserOut.model_validate(user)


@users_router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int, payload: UserUpdate, db: DbSession, actor: User = Depends(require_admin)
) -> UserOut:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    if payload.role is not None:
        if payload.role not in set(Role):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown role '{payload.role}'.")
        if user.id == actor.id and payload.role != Role.ADMIN:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot remove your own administrator role.")
        user.role = payload.role
    if payload.is_active is not None:
        if user.id == actor.id and not payload.is_active:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate your own account.")
        user.is_active = payload.is_active
    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.email is not None:
        user.email = payload.email
    if payload.password:
        user.password_hash = hash_password(payload.password)
    record_event(
        db,
        event_type="user_updated",
        actor=actor,
        entity_type="user",
        entity_id=user.id,
        message=f"Updated user {user.username}",
        payload=payload.model_dump(exclude_none=True, exclude={"password"}),
    )
    return UserOut.model_validate(user)
