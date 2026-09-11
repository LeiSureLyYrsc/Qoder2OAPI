import base64
from datetime import datetime, timezone
import hashlib
import os
import time
import urllib.parse
import uuid
from typing import Any

from qoder2oapi.constants import DEVICE_POLL, LOGIN, USERINFO_URL
from qoder2oapi.http import get_http_client
from qoder2oapi.models import TokenRecord
from qoder2oapi.token_store import token_store


class OAuthSession:
    def __init__(self, login_id: str, verifier: str, challenge: str, nonce: str, machine_id: str):
        self.login_id = login_id
        self.verifier = verifier
        self.challenge = challenge
        self.nonce = nonce
        self.machine_id = machine_id
        self.created_at = time.time()


_pending_sessions: dict[str, OAuthSession] = {}


def _base64url_no_pad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def start_device_flow() -> dict[str, str]:
    verifier_bytes = os.urandom(32)
    verifier = _base64url_no_pad(verifier_bytes)
    challenge = _base64url_no_pad(hashlib.sha256(verifier.encode("utf-8")).digest())
    nonce = str(uuid.uuid4())
    machine_id = str(uuid.uuid4())
    login_id = str(uuid.uuid4())

    session = OAuthSession(
        login_id=login_id,
        verifier=verifier,
        challenge=challenge,
        nonce=nonce,
        machine_id=machine_id,
    )
    _pending_sessions[login_id] = session

    params = {
        "challenge": challenge,
        "challenge_method": "S256",
        "machine_id": machine_id,
        "nonce": nonce,
    }
    verification_uri = f"{LOGIN}?{urllib.parse.urlencode(params)}"
    return {
        "login_id": login_id,
        "verification_uri": verification_uri,
        "machine_id": machine_id,
    }


def _parse_expiry(data: dict[str, Any]) -> int:
    now_ms = int(time.time() * 1000)
    raw_exp = data.get("expires_at") or data.get("expiresAt")
    if isinstance(raw_exp, (int, float)):
        val = int(raw_exp)
        return val if val > 10_000_000_000 else val * 1000
    if isinstance(raw_exp, str):
        if raw_exp.isdigit():
            val = int(raw_exp)
            return val if val > 10_000_000_000 else val * 1000
        try:
            dt = datetime.fromisoformat(raw_exp.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except Exception:
            pass

    raw_in = data.get("expires_in") or data.get("expiresIn")
    if isinstance(raw_in, (int, float, str)):
        try:
            return now_ms + int(float(raw_in) * 1000)
        except Exception:
            pass

    return now_ms + 30 * 24 * 3600 * 1000


async def poll_device_flow(login_id: str) -> dict[str, Any]:
    session = _pending_sessions.get(login_id)
    if not session:
        return {"status": "not_found", "message": "Login session not found or expired"}

    client = get_http_client()
    params = {
        "nonce": session.nonce,
        "verifier": session.verifier,
        "challenge_method": "S256",
    }
    headers = {
        "User-Agent": "Go-http-client/2.0",
        "Accept": "application/json",
    }

    try:
        resp = await client.get(DEVICE_POLL, params=params, headers=headers)
    except Exception as e:
        return {"status": "error", "message": f"Network error during poll: {e}"}

    if resp.status_code in (202, 404):
        return {"status": "pending"}

    if resp.status_code == 200:
        data = resp.json()
        token_data = data.get("data") if isinstance(data.get("data"), dict) else data
        token = token_data.get("token") or token_data.get("access_token")
        if not token:
            return {"status": "pending"}

        refresh_token = token_data.get("refresh_token") or token_data.get("refreshToken", "")
        user_id = str(token_data.get("user_id") or token_data.get("userId", ""))
        expires_at = _parse_expiry(token_data)

        user_info = await fetch_userinfo(token)
        name = user_info.get("name") or user_info.get("nickname", "")
        email = user_info.get("email", "")
        if not user_id:
            user_id = str(user_info.get("user_id") or user_info.get("userId", ""))

        record = TokenRecord(
            id=str(uuid.uuid4()),
            kind="oauth",
            access_token=token,
            refresh_token=refresh_token,
            user_id=user_id,
            name=name,
            email=email,
            machine_id=session.machine_id,
            expires_at=expires_at,
        )
        saved = token_store.upsert(record)
        _pending_sessions.pop(login_id, None)
        return {"status": "ok", "user": saved.public_dump()}

    return {"status": "failed", "code": resp.status_code, "text": resp.text}


async def fetch_userinfo(token: str) -> dict[str, Any]:
    client = get_http_client()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    try:
        resp = await client.get(USERINFO_URL, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data.get("data"), dict):
                return data["data"]
            return data
    except Exception:
        pass
    return {}
