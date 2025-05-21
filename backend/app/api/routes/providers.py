"""AI provider configuration endpoints.

Security: API keys are accepted here, encrypted at rest and **never** returned.
Only the last four characters are echoed back for identification.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.config import get_settings
from app.deps import DbSession, record_event, require_admin
from app.models.entities import ProviderConfig, User
from app.schemas import MessageOut, ProviderConfigIn, ProviderConfigOut, ProviderTestIn
from app.security import decrypt_secret
from app.services.llm.base import LLMError, ProviderSettings
from app.services.llm.providers import REGISTRY, build_adapter
from app.services.llm.service import (
    ResponseCache,
    available_providers,
    get_active_config,
    settings_from_config,
    upsert_provider_config,
)

router = APIRouter(prefix="/providers", tags=["ai-providers"])


@router.get("/catalogue")
def list_provider_types(actor: User = Depends(require_admin)) -> dict:
    settings = get_settings()
    return {
        "providers": available_providers(),
        "env_default": {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "api_key_configured": bool(settings.llm_api_key),
            "allow_paid_requests": settings.llm_allow_paid_requests,
        },
        "notes": [
            "The deterministic offline engine is always available and requires no credentials.",
            "A ChatGPT subscription does not include OpenAI API access; a platform API key with billing is required.",
            "No chargeable request is ever sent unless 'allow paid requests' is enabled for the active configuration.",
        ],
    }


@router.get("", response_model=list[ProviderConfigOut])
def list_configs(db: DbSession, actor: User = Depends(require_admin)) -> list[ProviderConfigOut]:
    rows = db.scalars(select(ProviderConfig).order_by(ProviderConfig.id)).all()
    return [ProviderConfigOut.model_validate(r) for r in rows]


@router.put("", response_model=ProviderConfigOut)
def save_config(
    payload: ProviderConfigIn, db: DbSession, actor: User = Depends(require_admin)
) -> ProviderConfigOut:
    if payload.provider_type not in REGISTRY:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unknown provider '{payload.provider_type}'. Available: {', '.join(sorted(REGISTRY))}.",
        )
    adapter_cls = REGISTRY[payload.provider_type]
    existing = db.scalar(select(ProviderConfig).where(ProviderConfig.name == payload.name))
    has_stored_key = bool(existing and existing.api_key_encrypted)
    if adapter_cls.requires_api_key and not payload.api_key and not has_stored_key:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Provider '{payload.provider_type}' requires an API key.",
        )
    if payload.provider_type == "custom" and not payload.base_url:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A base URL is required for a custom OpenAI-compatible provider.",
        )

    config = upsert_provider_config(
        db,
        name=payload.name,
        provider_type=payload.provider_type,
        base_url=payload.base_url,
        model=payload.model,
        api_key=payload.api_key,
        timeout_seconds=payload.timeout_seconds,
        max_tokens=payload.max_tokens,
        temperature=payload.temperature,
        allow_paid_requests=payload.allow_paid_requests,
        request_budget_usd=payload.request_budget_usd,
        token_budget=payload.token_budget,
        make_active=payload.make_active,
    )
    record_event(
        db,
        event_type="provider_config_saved",
        actor=actor,
        entity_type="provider",
        entity_id=config.id,
        message=f"Saved AI provider '{config.name}' ({config.provider_type}, model={config.model})",
        payload={"allow_paid_requests": config.allow_paid_requests, "active": config.is_active},
    )
    return ProviderConfigOut.model_validate(config)


@router.post("/test")
async def test_provider(payload: ProviderTestIn, db: DbSession, actor: User = Depends(require_admin)) -> dict:
    from app.services.llm.service import test_connection

    if payload.config_id is not None:
        config = db.get(ProviderConfig, payload.config_id)
        if config is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider configuration not found.")
        settings = settings_from_config(config)
    else:
        if not payload.provider_type:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "provider_type is required.")
        app_settings = get_settings()
        stored = db.scalar(
            select(ProviderConfig).where(ProviderConfig.provider_type == payload.provider_type)
        )
        settings = ProviderSettings(
            provider_type=payload.provider_type,
            model=payload.model or "",
            base_url=payload.base_url or "",
            api_key=payload.api_key or (decrypt_secret(stored.api_key_encrypted or "") if stored else ""),
            timeout_seconds=app_settings.llm_timeout_seconds,
            allow_paid_requests=bool(stored.allow_paid_requests) if stored else False,
            request_budget_usd=stored.request_budget_usd if stored else 0.0,
            spent_usd=stored.spent_usd if stored else 0.0,
            app_referer=app_settings.llm_app_referer,
            app_title=app_settings.llm_app_title,
        )
        config = stored

    if (
        payload.run_inference
        and not settings.allow_paid_requests
        and REGISTRY[settings.provider_type].is_paid
    ):
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            "A live inference test would incur cost. Enable 'allow paid requests' on the configuration first.",
        )

    result = await test_connection(settings, run_inference=payload.run_inference)
    if config is not None:
        config.last_test_at = datetime.now(UTC)
        config.last_test_ok = bool(result.get("ok"))
        config.last_test_message = str(result.get("message", ""))[:1000]
        config.capabilities = {
            "models_discovered": result.get("models_discovered"),
            "model_available": result.get("model_available"),
            "inference_ok": result.get("inference_ok"),
        }
        db.flush()
    record_event(
        db,
        event_type="provider_tested",
        actor=actor,
        entity_type="provider",
        entity_id=config.id if config else None,
        message=f"Tested provider {settings.provider_type}: ok={result.get('ok')}",
        payload={"run_inference": payload.run_inference, "paid": result.get("paid_request_made", False)},
    )
    return result


@router.post("/models")
async def discover_models(
    payload: ProviderTestIn,
    db: DbSession,
    actor: User = Depends(require_admin),
) -> dict:
    """List models the provider actually exposes (no invented identifiers).

    This is a POST, and the credentials travel in the body, because discovery
    has to work *before* a configuration is saved - that is the whole point of
    the button next to the model field. Sending an API key as a query string
    would leak it into access logs and browser history.

    Precedence mirrors ``POST /providers/test``: an explicit ``config_id`` wins,
    otherwise whatever the operator has typed is used, falling back to the
    stored values for the same provider type. A blank key therefore means
    "reuse the key already saved", matching the hint shown in the UI.
    """
    if payload.config_id is not None:
        config = db.get(ProviderConfig, payload.config_id)
        if config is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider configuration not found.")
        settings = settings_from_config(config)
    elif payload.provider_type:
        app_settings = get_settings()
        stored = db.scalar(
            select(ProviderConfig).where(ProviderConfig.provider_type == payload.provider_type)
        )
        settings = ProviderSettings(
            provider_type=payload.provider_type,
            model=payload.model or (stored.model if stored else "") or "",
            base_url=payload.base_url or (stored.base_url if stored else "") or "",
            api_key=payload.api_key or (decrypt_secret(stored.api_key_encrypted or "") if stored else ""),
            timeout_seconds=app_settings.llm_timeout_seconds,
            app_referer=app_settings.llm_app_referer,
            app_title=app_settings.llm_app_title,
        )
    else:
        config = get_active_config(db)
        if config is None:
            return {"provider": "offline", "models": [{"id": "offline-deterministic"}], "source": "builtin"}
        settings = settings_from_config(config)

    adapter = build_adapter(settings)
    try:
        models = await adapter.list_models()
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"{exc.user_message} ({exc})") from exc
    return {
        "provider": settings.provider_type,
        "models": [m.to_dict() for m in models],
        "count": len(models),
        "source": "provider_catalogue",
    }


@router.post("/cache/clear", response_model=MessageOut)
def clear_cache(actor: User = Depends(require_admin)) -> MessageOut:
    removed = ResponseCache().clear()
    return MessageOut(message=f"Cleared {removed} cached LLM response(s).")


@router.delete("/{config_id}", response_model=MessageOut)
def delete_config(config_id: int, db: DbSession, actor: User = Depends(require_admin)) -> MessageOut:
    config = db.get(ProviderConfig, config_id)
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider configuration not found.")
    name = config.name
    db.delete(config)
    record_event(
        db, event_type="provider_config_deleted", actor=actor, message=f"Deleted provider config '{name}'"
    )
    return MessageOut(message=f"Provider configuration '{name}' deleted.")
