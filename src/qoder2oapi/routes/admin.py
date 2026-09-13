from typing import Any
import secrets
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from qoder2oapi.auth_proxy import verify_api_key
from qoder2oapi.catalog import catalog_manager
from qoder2oapi.config import api_key_file, settings
from qoder2oapi.names import public_id_for_key, public_name_for_key
from qoder2oapi.oauth import poll_device_flow, start_device_flow
from qoder2oapi.pool import pool
from qoder2oapi.quota import fetch_quota
from qoder2oapi.refresh import ensure_fresh, exchange_pat
from qoder2oapi.token_store import token_store

router = APIRouter(prefix="/api/admin", dependencies=[Depends(verify_api_key)])


class RotateKeyResponse(BaseModel):
    api_key: str


class PatLoginRequest(BaseModel):
    pat: str


class UpdateAccountRequest(BaseModel):
    enabled: bool | None = None
    skip_quota: bool | None = None
    skip_auth: bool | None = None


class ImportAccountsRequest(BaseModel):
    mode: str = Field(default="merge")
    accounts: list[dict[str, Any]] | None = None


@router.post("/login/start")
async def admin_login_start() -> dict[str, str]:
    return start_device_flow()


@router.get("/login/poll")
async def admin_login_poll(login_id: str) -> dict[str, Any]:
    return await poll_device_flow(login_id)


@router.get("/status")
async def admin_status() -> dict[str, Any]:
    accounts = token_store.list_accounts()
    counts = pool.counts()
    token = token_store.load_token()
    is_logged_in = counts["usable"] > 0 or counts["total"] > 0
    user_data = token.public_dump() if token else None

    return {
        "is_logged_in": is_logged_in,
        "user": user_data,
        "account_count": counts["total"],
        "usable_count": counts["usable"],
        "skip_quota_count": counts["skip_quota"],
        "skip_auth_count": counts["skip_auth"],
        "catalog_models_count": len(catalog_manager.models_by_key),
        "catalog_last_updated": catalog_manager.last_updated,
        "proxy_host": settings.qoder2oapi_host,
        "proxy_port": settings.qoder2oapi_port,
        "accounts": [acc.public_dump() for acc in accounts],
    }


@router.get("/accounts")
async def list_accounts() -> dict[str, Any]:
    accounts = token_store.list_accounts()
    return {"accounts": [acc.public_dump() for acc in accounts]}


@router.get("/accounts/export")
async def export_accounts() -> JSONResponse:
    payload = token_store.export_payload()
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": "attachment; filename=qoder2oapi-accounts.json",
        },
    )


@router.post("/accounts/import")
async def import_accounts(body: ImportAccountsRequest) -> dict[str, Any]:
    mode = (body.mode or "merge").strip().lower()
    if mode not in ("merge", "replace"):
        raise HTTPException(status_code=400, detail="mode 只能是 merge 或 replace")
    raw: Any = {"accounts": body.accounts or []}
    result = token_store.import_accounts(raw, mode=mode)
    return {"status": "ok", **result}


@router.post("/accounts/pat")
async def add_pat_account(body: PatLoginRequest) -> dict[str, Any]:
    if not body.pat:
        raise HTTPException(status_code=400, detail="PAT token is required")
    try:
        record = await exchange_pat(body.pat)
        saved = token_store.upsert(record)
        try:
            await catalog_manager.fetch_models()
        except Exception:
            pass
        return {"status": "ok", "account": saved.public_dump()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to add PAT account: {e}")


@router.patch("/accounts/{account_id}")
async def update_account_flags(account_id: str, body: UpdateAccountRequest) -> dict[str, Any]:
    updated = pool.update_flags(
        account_id,
        enabled=body.enabled,
        skip_quota=body.skip_quota,
        skip_auth=body.skip_auth,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"status": "ok", "account": updated.public_dump()}


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str) -> dict[str, Any]:
    success = token_store.delete(account_id)
    if not success:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"status": "ok", "message": "Account deleted"}


@router.post("/accounts/{account_id}/refresh")
async def refresh_pat_account(account_id: str) -> dict[str, Any]:
    acc = token_store.get(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    if acc.kind != "pat":
        raise HTTPException(status_code=400, detail="Only PAT accounts can be manually refreshed")

    # Force refresh by expiring the account record check
    acc.expires_at = 0
    refreshed = await ensure_fresh(acc)
    if refreshed.skip_auth:
        raise HTTPException(status_code=400, detail=f"Refresh failed: {refreshed.last_error}")
    return {"status": "ok", "account": refreshed.public_dump()}


@router.get("/quota")
async def admin_quota() -> dict[str, Any]:
    return await fetch_quota()


@router.get("/models")
async def admin_models() -> dict[str, Any]:
    models = await catalog_manager.fetch_models()
    cards = []
    for item in models:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        thinking = catalog_manager.determine_thinking_levels(item)
        key = str(item.get("key"))
        cards.append(
            {
                "key": key,
                "public_id": public_id_for_key(key),
                "display_name": public_name_for_key(key, item.get("display_name") or key),
                "is_reasoning": bool(item.get("is_reasoning")),
                "is_vl": bool(item.get("is_vl")),
                "max_input_tokens": catalog_manager.get_max_context_length(item),
                "min_input_tokens": catalog_manager.get_min_context_length(item),
                "max_output_tokens": catalog_manager.get_max_output_tokens(item),
                "thinking_levels": thinking,
                "default_thinking": catalog_manager.get_default_thinking(item),
                "enable": item.get("enable", True),
            }
        )
    return {"models": cards}


@router.get("/api-key")
async def get_current_api_key() -> dict[str, str]:
    return {"api_key": settings.qoder2oapi_api_key}


@router.post("/api-key/rotate", response_model=RotateKeyResponse)
async def rotate_api_key() -> RotateKeyResponse:
    new_key = secrets.token_urlsafe(32)
    settings.qoder2oapi_api_key = new_key
    api_key_file.write_text(new_key, encoding="utf-8")
    return RotateKeyResponse(api_key=new_key)
