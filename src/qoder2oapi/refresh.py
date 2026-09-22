import asyncio
import time
import uuid
from typing import Any
from qoder2oapi.constants import (
    CLIENT_CLI,
    JOB_TOKEN_EXCHANGE,
    JOB_TOKEN_REFRESH,
)
from qoder2oapi.http import get_http_client
from qoder2oapi.identity import normalize_client
from qoder2oapi.models import AccountRecord
from qoder2oapi.oauth import _parse_expiry, extract_user_id, fetch_userinfo
from qoder2oapi.runtime_settings import runtime_settings
from qoder2oapi.token_store import token_store

PAT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "qodercli/1.0.0",
    "Cosy-Version": "1.0.0",
    "Cosy-ClientType": "5",
}

_refresh_locks: dict[str, asyncio.Lock] = {}


def _extract_access_token(data: dict[str, Any]) -> str:
    return str(
        data.get("token")
        or data.get("access_token")
        or data.get("job_token")
        or data.get("jobToken")
        or data.get("jt")
        or ""
    )


async def exchange_pat(pat: str) -> AccountRecord:
    client = get_http_client()
    body = {"personal_token": pat}
    resp = await client.post(JOB_TOKEN_EXCHANGE, headers=PAT_HEADERS, json=body)
    if resp.status_code != 200:
        raise ValueError(f"PAT exchange failed with status {resp.status_code}: {resp.text}")

    data = resp.json()
    token_data = data.get("data") if isinstance(data.get("data"), dict) else data
    access_token = _extract_access_token(token_data)
    if not access_token:
        raise ValueError(f"No token in exchange response: {data}")

    refresh_token = token_data.get("refresh_token") or token_data.get("refreshToken", "")
    expires_at = _parse_expiry(token_data, expires_in_unit="milliseconds")
    user_id = extract_user_id(token_data)

    name = ""
    email = ""
    user_info = await fetch_userinfo(access_token)
    if user_info:
        name = user_info.get("name") or user_info.get("nickname", "")
        email = user_info.get("email", "")
        if not user_id:
            user_id = extract_user_id(user_info, include_id=True)

    if not user_id:
        raise ValueError("PAT exchange succeeded but Qoder user ID could not be resolved")

    machine_id = str(uuid.uuid4())
    record = AccountRecord(
        id=str(uuid.uuid4()),
        kind="pat",
        client=CLIENT_CLI,
        access_token=access_token,
        refresh_token=refresh_token,
        pat=pat,
        user_id=user_id,
        name=name,
        email=email,
        machine_id=machine_id,
        expires_at=expires_at,
        enabled=True,
        skip_quota=False,
        skip_auth=False,
    )
    return record


def needs_refresh(account: AccountRecord) -> bool:
    return account.kind == "pat"


async def ensure_fresh(account: AccountRecord, force: bool = False) -> AccountRecord:
    account.client = normalize_client(account.client)
    if not needs_refresh(account):
        return account

    lock_key = account.id or account.user_id or account.pat
    lock = _refresh_locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        latest = token_store.get(account.id) if account.id else None
        if latest and latest.access_token != account.access_token:
            return latest
        return await _ensure_fresh_locked(account, force=force)


def _mark_auth_failed(account: AccountRecord, error: str) -> AccountRecord:
    account.last_error = error
    if runtime_settings.auto_mark_auth:
        account.skip_auth = True
    token_store.upsert(account)
    return account


async def _ensure_fresh_locked(account: AccountRecord, force: bool = False) -> AccountRecord:

    if not account.user_id:
        user_info = await fetch_userinfo(account.access_token)
        user_id = extract_user_id(user_info, include_id=True)
        if user_id:
            account.user_id = user_id
            account.name = str(user_info.get("name") or user_info.get("nickname") or account.name)
            account.email = str(user_info.get("email") or account.email)
            account.skip_auth = False
            account.last_error = ""
            token_store.upsert(account)

    now_ms = int(time.time() * 1000)
    # If expires_at - now > 5 minutes (300,000 ms): return unchanged
    if not force and account.expires_at - now_ms > 300_000 and account.user_id:
        if account.skip_auth or account.last_error:
            account.skip_auth = False
            account.last_error = ""
            token_store.upsert(account)
        return account

    client = get_http_client()
    refreshed_ok = False
    new_access_token = ""
    new_refresh_token = ""
    new_expires_at = 0

    # Try JOB_TOKEN_REFRESH with Bearer access_token and {"refresh_token": refresh_token}
    if account.refresh_token:
        headers = dict(PAT_HEADERS)
        headers["Authorization"] = f"Bearer {account.access_token}"
        try:
            resp = await client.post(
                JOB_TOKEN_REFRESH,
                headers=headers,
                json={"refresh_token": account.refresh_token},
            )
            if resp.status_code == 200:
                data = resp.json()
                token_data = data.get("data") if isinstance(data.get("data"), dict) else data
                new_access_token = _extract_access_token(token_data)
                if new_access_token:
                    new_refresh_token = (
                        token_data.get("refresh_token")
                        or token_data.get("refreshToken")
                        or account.refresh_token
                    )
                    new_expires_at = _parse_expiry(token_data, expires_in_unit="milliseconds")
                    refreshed_ok = True
        except Exception:
            refreshed_ok = False

    # On failure, JOB_TOKEN_EXCHANGE with stored pat
    if not refreshed_ok:
        if not account.pat:
            account.last_error = "Missing PAT to re-exchange"
            if runtime_settings.auto_mark_auth:
                account.skip_auth = True
            token_store.upsert(account)
            return account

        try:
            resp = await client.post(
                JOB_TOKEN_EXCHANGE,
                headers=PAT_HEADERS,
                json={"personal_token": account.pat},
            )
            if resp.status_code == 200:
                data = resp.json()
                token_data = data.get("data") if isinstance(data.get("data"), dict) else data
                new_access_token = _extract_access_token(token_data)
                if new_access_token:
                    new_refresh_token = (
                        token_data.get("refresh_token")
                        or token_data.get("refreshToken", "")
                    )
                    new_expires_at = _parse_expiry(token_data, expires_in_unit="milliseconds")
                    refreshed_ok = True
            else:
                account.last_error = f"Re-exchange failed: HTTP {resp.status_code}"
                if runtime_settings.auto_mark_auth:
                    account.skip_auth = True
                token_store.upsert(account)
                return account
        except Exception as e:
            account.last_error = f"Re-exchange failed: {e}"
            if runtime_settings.auto_mark_auth:
                account.skip_auth = True
            token_store.upsert(account)
            return account

    if refreshed_ok:
        account.access_token = new_access_token
        if new_refresh_token:
            account.refresh_token = new_refresh_token
        account.expires_at = new_expires_at
        account.client = normalize_client(account.client)
        if not account.user_id:
            user_info = await fetch_userinfo(account.access_token)
            user_id = extract_user_id(user_info, include_id=True)
            if user_id:
                account.user_id = user_id
                account.name = str(user_info.get("name") or user_info.get("nickname") or account.name)
                account.email = str(user_info.get("email") or account.email)
        if not account.user_id:
            account.last_error = "Qoder user ID could not be resolved for PAT account"
            if runtime_settings.auto_mark_auth:
                account.skip_auth = True
            token_store.upsert(account)
            return account
        account.skip_auth = False
        account.last_error = ""
        token_store.upsert(account)
        return account

    account.last_error = "Failed to refresh or exchange PAT"
    if runtime_settings.auto_mark_auth:
        account.skip_auth = True
    token_store.upsert(account)
    return account
