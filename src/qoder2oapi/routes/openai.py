from typing import Any
from fastapi import APIRouter, Depends, HTTPException

from qoder2oapi.auth_proxy import verify_api_key
from qoder2oapi.catalog import catalog_manager
from qoder2oapi.infer import execute_infer
from qoder2oapi.models import ChatCompletionRequest, ModelCard, ModelListResponse
from qoder2oapi.names import public_id_for_key
from qoder2oapi.pool import pool
from qoder2oapi.quota import fetch_credits, fetch_quota
from qoder2oapi.refresh import ensure_fresh, needs_refresh
from qoder2oapi.token_store import token_store

router = APIRouter(prefix="/v1", dependencies=[Depends(verify_api_key)])


@router.get("/models", response_model=ModelListResponse)
async def list_models() -> ModelListResponse:
    raw_models = await catalog_manager.fetch_models(force_cli_identity=True)
    source = raw_models or [
        {"key": k} for k in catalog_manager.models_by_key.keys()
    ]
    cards = []
    seen: set[str] = set()
    for m in source:
        key = m.get("key") if isinstance(m, dict) else None
        if not key:
            continue
        public_id = public_id_for_key(str(key))
        if public_id in seen:
            continue
        seen.add(public_id)
        cards.append(ModelCard(id=public_id, owned_by="qoder-cn"))
    return ModelListResponse(data=cards)


@router.post("/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    usable_accounts = pool.peek_usable()
    if not usable_accounts:
        for account in token_store.list_accounts():
            if needs_refresh(account) and account.enabled and account.skip_auth:
                await ensure_fresh(account, force=True)
        usable_accounts = pool.peek_usable()
    if not usable_accounts:
        # Check if all accounts are only skipped for quota
        all_accounts = token_store.list_accounts()
        if all_accounts and all(acc.skip_quota and not acc.skip_auth for acc in all_accounts):
            raise HTTPException(status_code=429, detail="No usable Qoder account: all accounts exceeded quota.")
        raise HTTPException(status_code=401, detail="No usable Qoder account. Please log in.")

    if not catalog_manager.get_model(request.model):
        await catalog_manager.fetch_models()

    return await execute_infer(request, stream=bool(request.stream))


@router.get("/dashboard/billing/usage")
async def billing_usage() -> dict[str, Any]:
    quota = await fetch_quota()
    return {
        "object": "list",
        "total_usage": quota.get("total_usage", 0.0),
        "daily_costs": [],
    }


@router.get("/dashboard/billing/credits")
async def billing_credits() -> dict[str, Any]:
    return await fetch_credits()


@router.get("/dashboard/billing/subscription")
async def billing_subscription() -> dict[str, Any]:
    quota = await fetch_quota()
    expires_at = quota.get("expires_at", 0)
    access_until = int(expires_at / 1000) if expires_at else 0
    return {
        "object": "billing_subscription",
        "has_payment_method": True,
        "canceled": False,
        "canceled_at": None,
        "delinquent": None,
        "access_until": access_until,
        "soft_limit": quota.get("hard_limit", 0.0),
        "hard_limit": quota.get("hard_limit", 0.0),
        "system_hard_limit": quota.get("hard_limit", 0.0),
        "soft_limit_usd": quota.get("hard_limit", 0.0),
        "hard_limit_usd": quota.get("hard_limit", 0.0),
        "system_hard_limit_usd": quota.get("hard_limit", 0.0),
        "plan": {
            "title": quota.get("user_type", "personal"),
            "id": quota.get("user_type", "personal"),
        },
    }
