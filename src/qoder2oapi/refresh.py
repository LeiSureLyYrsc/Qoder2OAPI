import time
import uuid
from typing import Any
from qoder2oapi.constants import JOB_TOKEN_EXCHANGE, JOB_TOKEN_REFRESH
from qoder2oapi.http import get_http_client
from qoder2oapi.models import AccountRecord
from qoder2oapi.oauth import _parse_expiry, fetch_userinfo
from qoder2oapi.token_store import token_store

PAT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "qodercli/1.0.0",
    "Cosy-Version": "1.0.0",
    "Cosy-ClientType": "5",
}


async def exchange_pat(pat: str) -> AccountRecord:
    client = get_http_client()
    body = {"personal_token": pat}
    resp = await client.post(JOB_TOKEN_EXCHANGE, headers=PAT_HEADERS, json=body)
    if resp.status_code != 200:
        raise ValueError(f"PAT exchange failed with status {resp.status_code}: {resp.text}")

    data = resp.json()
    token_data = data.get("data") if isinstance(data.get("data"), dict) else data
    access_token = token_data.get("token") or token_data.get("access_token")
    if not access_token:
        raise ValueError(f"No token in exchange response: {data}")

    refresh_token = token_data.get("refresh_token") or token_data.get("refreshToken", "")
    expires_at = _parse_expiry(token_data)
    user_id = str(token_data.get("user_id") or token_data.get("userId", ""))

    name = ""
    email = ""
    user_info = await fetch_userinfo(access_token)
    if user_info:
        name = user_info.get("name") or user_info.get("nickname", "")
        email = user_info.get("email", "")
        if not user_id:
            user_id = str(user_info.get("user_id") or user_info.get("userId", ""))

    machine_id = str(uuid.uuid4())
    record = AccountRecord(
        id=str(uuid.uuid4()),
        kind="pat",
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


async def ensure_fresh(account: AccountRecord) -> AccountRecord:
    if account.kind != "pat":
        return account

    now_ms = int(time.time() * 1000)
    # If expires_at - now > 5 minutes (300,000 ms): return unchanged
    if account.expires_at - now_ms > 300_000:
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
                new_access_token = token_data.get("token") or token_data.get("access_token")
                if new_access_token:
                    new_refresh_token = (
                        token_data.get("refresh_token")
                        or token_data.get("refreshToken")
                        or account.refresh_token
                    )
                    new_expires_at = _parse_expiry(token_data)
                    refreshed_ok = True
        except Exception:
            refreshed_ok = False

    # On failure, JOB_TOKEN_EXCHANGE with stored pat
    if not refreshed_ok:
        if not account.pat:
            account.skip_auth = True
            account.last_error = "Missing PAT to re-exchange"
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
                new_access_token = token_data.get("token") or token_data.get("access_token")
                if new_access_token:
                    new_refresh_token = (
                        token_data.get("refresh_token")
                        or token_data.get("refreshToken", "")
                    )
                    new_expires_at = _parse_expiry(token_data)
                    refreshed_ok = True
            else:
                account.skip_auth = True
                account.last_error = f"Re-exchange failed: HTTP {resp.status_code}"
                token_store.upsert(account)
                return account
        except Exception as e:
            account.skip_auth = True
            account.last_error = f"Re-exchange failed: {e}"
            token_store.upsert(account)
            return account

    if refreshed_ok:
        account.access_token = new_access_token
        if new_refresh_token:
            account.refresh_token = new_refresh_token
        account.expires_at = new_expires_at
        account.skip_auth = False
        account.last_error = ""
        token_store.upsert(account)
        return account

    account.skip_auth = True
    account.last_error = "Failed to refresh or exchange PAT"
    token_store.upsert(account)
    return account
