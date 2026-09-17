import asyncio
from typing import Any
from qoder2oapi.models import AccountRecord
from qoder2oapi.runtime_settings import runtime_settings
from qoder2oapi.token_store import token_store


class AccountPool:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cursor: int = 0

    @staticmethod
    def usable(acc: AccountRecord) -> bool:
        return bool(acc.enabled and not acc.skip_quota and not acc.skip_auth and acc.access_token)

    def peek_usable(self) -> list[AccountRecord]:
        return [acc for acc in token_store.list_accounts() if self.usable(acc)]

    async def next_account(self) -> AccountRecord | None:
        async with self._lock:
            accounts = token_store.list_accounts()
            usable_accounts = [acc for acc in accounts if self.usable(acc)]
            if not usable_accounts:
                return None

            n = len(usable_accounts)
            idx = self._cursor % n
            self._cursor = (idx + 1) % n
            return usable_accounts[idx]

    def mark_skip_quota(self, account_id: str, error: str = "") -> None:
        acc = token_store.get(account_id)
        if acc:
            if runtime_settings.auto_mark_quota:
                acc.skip_quota = True
            if error:
                acc.last_error = error
            token_store.upsert(acc)

    def mark_skip_auth(self, account_id: str, error: str = "") -> None:
        acc = token_store.get(account_id)
        if acc:
            if runtime_settings.auto_mark_auth:
                acc.skip_auth = True
            if error:
                acc.last_error = error
            token_store.upsert(acc)

    def update_flags(
        self,
        account_id: str,
        enabled: bool | None = None,
        skip_quota: bool | None = None,
        skip_auth: bool | None = None,
    ) -> AccountRecord | None:
        acc = token_store.get(account_id)
        if not acc:
            return None
        if enabled is not None:
            acc.enabled = enabled
        if skip_quota is not None:
            acc.skip_quota = skip_quota
        if skip_auth is not None:
            acc.skip_auth = skip_auth
        if not acc.skip_quota and not acc.skip_auth:
            acc.last_error = ""
        return token_store.upsert(acc)

    def on_settings_changed(
        self,
        prev_quota: bool,
        new_quota: bool,
        prev_auth: bool,
        new_auth: bool,
    ) -> None:
        if (prev_quota and not new_quota) or (prev_auth and not new_auth):
            accounts = token_store.list_accounts()
            for acc in accounts:
                changed = False
                if prev_quota and not new_quota and acc.skip_quota:
                    acc.skip_quota = False
                    changed = True
                if prev_auth and not new_auth and acc.skip_auth:
                    acc.skip_auth = False
                    changed = True
                if not acc.skip_quota and not acc.skip_auth and acc.last_error:
                    acc.last_error = ""
                    changed = True
                if changed:
                    token_store.upsert(acc)

    def counts(self) -> dict[str, int]:
        accounts = token_store.list_accounts()
        total = len(accounts)
        usable_cnt = sum(1 for acc in accounts if self.usable(acc))
        skip_quota_cnt = sum(1 for acc in accounts if acc.skip_quota)
        skip_auth_cnt = sum(1 for acc in accounts if acc.skip_auth)
        return {
            "total": total,
            "usable": usable_cnt,
            "skip_quota": skip_quota_cnt,
            "skip_auth": skip_auth_cnt,
        }


pool = AccountPool()
