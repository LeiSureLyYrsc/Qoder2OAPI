import json
import os
from pathlib import Path
import uuid
from typing import Any

from qoder2oapi.config import data_dir_path
from qoder2oapi.constants import CLIENT_CLI
from qoder2oapi.identity import normalize_client
from qoder2oapi.models import AccountRecord, TokenRecord

EXPORT_FIELDS = (
    "id",
    "kind",
    "client",
    "access_token",
    "refresh_token",
    "pat",
    "user_id",
    "name",
    "email",
    "machine_id",
    "expires_at",
    "enabled",
    "skip_quota",
    "skip_auth",
)

STORAGE_FIELDS = EXPORT_FIELDS + ("last_error",)


def _field_default(field: str) -> Any:
    if field == "expires_at":
        return 0
    if field == "client":
        return CLIENT_CLI
    return ""


def account_to_config(account: AccountRecord) -> dict[str, Any]:
    data = account.model_dump()
    return {field: data.get(field, _field_default(field)) for field in EXPORT_FIELDS}


def account_to_storage(account: AccountRecord) -> dict[str, Any]:
    data = account.model_dump()
    return {field: data.get(field, _field_default(field)) for field in STORAGE_FIELDS}


def parse_account_payload(raw: Any) -> AccountRecord | None:
    if not isinstance(raw, dict):
        return None
    access_token = str(raw.get("access_token") or raw.get("accessToken") or "").strip()
    if not access_token or access_token == "***":
        return None
    kind = str(raw.get("kind") or "oauth").strip().lower()
    if kind not in ("oauth", "pat"):
        kind = "oauth"
    user_id = str(raw.get("user_id") or raw.get("userId") or "")
    machine_id = str(raw.get("machine_id") or raw.get("machineId") or uuid.uuid4())
    account_id = str(raw.get("id") or uuid.uuid4())
    expires_at = raw.get("expires_at") or raw.get("expiresAt") or 0
    try:
        expires_at = int(expires_at)
    except (TypeError, ValueError):
        expires_at = 0
    pat = str(raw.get("pat") or "")
    if pat == "***":
        pat = ""
    refresh_token = str(raw.get("refresh_token") or raw.get("refreshToken") or "")
    if refresh_token == "***":
        refresh_token = ""
    return AccountRecord(
        id=account_id,
        kind=kind,
        client=normalize_client(raw.get("client")),
        access_token=access_token,
        refresh_token=refresh_token,
        pat=pat,
        user_id=user_id,
        name=str(raw.get("name") or ""),
        email=str(raw.get("email") or ""),
        machine_id=machine_id,
        expires_at=expires_at,
        enabled=bool(raw.get("enabled", True)),
        skip_quota=bool(raw.get("skip_quota") or raw.get("skipQuota", False)),
        skip_auth=bool(raw.get("skip_auth") or raw.get("skipAuth", False)),
        last_error=str(raw.get("last_error") or raw.get("lastError") or ""),
    )


class TokenStore:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or data_dir_path
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.accounts_file = self.data_dir / "accounts.json"

    def list_accounts(self) -> list[AccountRecord]:
        if not self.accounts_file.exists():
            return []
        try:
            raw = json.loads(self.accounts_file.read_text(encoding="utf-8"))
        except Exception:
            return []
        items = raw.get("accounts") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return []
        accounts = []
        for item in items:
            parsed = parse_account_payload(item)
            if parsed:
                accounts.append(parsed)
        return accounts

    def export_payload(self) -> dict[str, Any]:
        return {
            "version": 1,
            "accounts": [account_to_config(acc) for acc in self.list_accounts()],
        }

    def save_all(self, accounts: list[AccountRecord]) -> None:
        payload = {
            "version": 1,
            "accounts": [account_to_storage(acc) for acc in accounts],
        }
        self.accounts_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def get(self, account_id: str) -> AccountRecord | None:
        for acc in self.list_accounts():
            if acc.id == account_id:
                return acc
        return None

    def upsert(self, account: AccountRecord) -> AccountRecord:
        accounts = self.list_accounts()
        if not account.id:
            account.id = str(uuid.uuid4())

        found_idx = -1
        for idx, existing in enumerate(accounts):
            if existing.id == account.id:
                found_idx = idx
                break
            if account.user_id and existing.user_id == account.user_id:
                account.id = existing.id
                found_idx = idx
                break

        if found_idx >= 0:
            accounts[found_idx] = account
        else:
            accounts.append(account)

        self.save_all(accounts)
        return account

    def delete(self, account_id: str) -> bool:
        accounts = self.list_accounts()
        new_list = [acc for acc in accounts if acc.id != account_id]
        if len(new_list) == len(accounts):
            return False
        self.save_all(new_list)
        return True

    def load_token(self) -> AccountRecord | None:
        accounts = self.list_accounts()
        if not accounts:
            return None
        for acc in accounts:
            if acc.enabled and not acc.skip_quota and not acc.skip_auth and acc.access_token:
                return acc
        return accounts[0]

    def save_token(self, token: TokenRecord | AccountRecord) -> AccountRecord:
        if isinstance(token, AccountRecord):
            account = token
            if not account.id:
                account.id = str(uuid.uuid4())
            if not account.kind:
                account.kind = "oauth"
        else:
            dump = token.model_dump()
            account = AccountRecord(
                id=str(uuid.uuid4()),
                kind="oauth",
                **dump,
            )
        return self.upsert(account)

    def import_accounts(self, raw: Any, mode: str = "merge") -> dict[str, int]:
        items: list[Any]
        if isinstance(raw, dict):
            items = raw.get("accounts") or raw.get("data") or []
        elif isinstance(raw, list):
            items = raw
        else:
            items = []

        incoming: list[AccountRecord] = []
        skipped = 0
        for item in items:
            parsed = parse_account_payload(item)
            if parsed:
                incoming.append(parsed)
            else:
                skipped += 1

        if mode == "replace":
            self.save_all(incoming)
            return {
                "imported": len(incoming),
                "updated": 0,
                "skipped": skipped,
                "total": len(incoming),
            }

        existing = self.list_accounts()
        by_id = {acc.id: i for i, acc in enumerate(existing)}
        by_user = {acc.user_id: i for i, acc in enumerate(existing) if acc.user_id}
        updated = 0
        imported = 0
        for acc in incoming:
            idx = by_id.get(acc.id)
            if idx is None and acc.user_id:
                idx = by_user.get(acc.user_id)
            if idx is not None:
                acc.id = existing[idx].id
                existing[idx] = acc
                by_id[acc.id] = idx
                if acc.user_id:
                    by_user[acc.user_id] = idx
                updated += 1
            else:
                if not acc.id:
                    acc.id = str(uuid.uuid4())
                existing.append(acc)
                by_id[acc.id] = len(existing) - 1
                if acc.user_id:
                    by_user[acc.user_id] = len(existing) - 1
                imported += 1
        self.save_all(existing)
        return {
            "imported": imported,
            "updated": updated,
            "skipped": skipped,
            "total": len(existing),
        }

    def clear(self) -> None:
        if self.accounts_file.exists():
            os.remove(self.accounts_file)


token_store = TokenStore()
