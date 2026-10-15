import asyncio
import time
from typing import Any
from qoder2oapi.catalog import catalog_manager
from qoder2oapi.models import AccountRecord
from qoder2oapi.names import alias_to_internal_key
from qoder2oapi.runtime_settings import runtime_settings
from qoder2oapi.token_store import token_store


def is_zero_cost_model(model_name_or_key: str) -> bool:
    """Check if model is zero-cost / free.
    
    Identified via:
    1. model_dispatch setting explicitly set to 'poll_include_quota'.
    2. Model key / name containing 'dfmodel', 'qfmodel', or 'lite'.
    3. Catalog price_factor <= 0 if present in model data.
    """
    key = str(model_name_or_key or "").strip()
    if not key:
        return False

    # Check model_dispatch setting first
    dispatch = runtime_settings.model_dispatch.get(key)
    if dispatch == "poll_include_quota":
        return True

    # Resolve internal key from catalog or aliases
    found = catalog_manager.get_model(key)
    internal_key = str(found.get("key") or "") if isinstance(found, dict) else ""
    if not internal_key:
        internal_key = alias_to_internal_key(key)

    if internal_key and runtime_settings.model_dispatch.get(internal_key) == "poll_include_quota":
        return True

    # Check price_factor in catalog model data
    if isinstance(found, dict):
        price_factor = found.get("price_factor")
        if price_factor is None:
            price_factor = (found.get("model_config") or {}).get("price_factor")
        if price_factor is not None:
            try:
                if float(price_factor) <= 0.0:
                    return True
            except (ValueError, TypeError):
                pass

    # Check known free model keys / substrings
    lowered_key = internal_key.lower() if internal_key else key.lower()
    for pattern in ("dfmodel", "qfmodel", "lite"):
        if pattern in lowered_key or pattern in key.lower():
            return True

    return False


class AccountPool:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cursor: int = 0
        self._active_id: str | None = None
        self._rate_limits: dict[str, float] = {}

    def mark_rate_limited(self, account_id: str, duration_seconds: float = 30.0) -> None:
        """Apply temporary in-memory circuit breaker cooldown without touching disk."""
        if not account_id:
            return
        cooldown_until = time.time() + max(0.0, float(duration_seconds))
        self._rate_limits[account_id] = cooldown_until

    def is_rate_limited(self, account_id: str) -> bool:
        """Check if account is currently in cooldown."""
        cooldown_until = self._rate_limits.get(account_id)
        if cooldown_until is None:
            return False
        if time.time() < cooldown_until:
            return True
        self._rate_limits.pop(account_id, None)
        return False

    def get_cooldown_remaining(self, account_id: str) -> float:
        """Return remaining cooldown seconds, or 0.0 if not rate limited."""
        cooldown_until = self._rate_limits.get(account_id)
        if cooldown_until is None:
            return 0.0
        remaining = cooldown_until - time.time()
        if remaining <= 0:
            self._rate_limits.pop(account_id, None)
            return 0.0
        return remaining

    def clear_cooldown(self, account_id: str | None = None) -> None:
        if account_id is None:
            self._rate_limits.clear()
        else:
            self._rate_limits.pop(account_id, None)

    @staticmethod
    def usable(acc: AccountRecord) -> bool:
        return bool(acc.enabled and not acc.skip_quota and not acc.skip_auth and acc.access_token)

    @staticmethod
    def eligible(acc: AccountRecord, include_quota: bool = False) -> bool:
        if not (acc.enabled and acc.access_token and not acc.skip_auth):
            return False
        if acc.skip_quota and not include_quota:
            return False
        return True

    def peek_usable(self) -> list[AccountRecord]:
        return [acc for acc in token_store.list_accounts() if self.usable(acc) and not self.is_rate_limited(acc.id)]

    def peek_eligible(self, include_quota: bool = False) -> list[AccountRecord]:
        return [
            acc
            for acc in token_store.list_accounts()
            if self.eligible(acc, include_quota) and not self.is_rate_limited(acc.id)
        ]

    async def next_account(self) -> AccountRecord | None:
        async with self._lock:
            accounts = token_store.list_accounts()
            usable_accounts = [acc for acc in accounts if self.usable(acc) and not self.is_rate_limited(acc.id)]
            if not usable_accounts:
                return None

            n = len(usable_accounts)
            idx = self._cursor % n
            self._cursor = (idx + 1) % n
            return usable_accounts[idx]

    async def candidate_accounts(
        self,
        strategy: str = "poll",
        include_quota: bool = False,
    ) -> list[AccountRecord]:
        async with self._lock:
            accounts = token_store.list_accounts()
            eligible = [acc for acc in accounts if self.eligible(acc, include_quota)]
            if not eligible:
                return []

            # Filter out rate-limited accounts unless all eligible are rate-limited
            non_rate_limited = [acc for acc in eligible if not self.is_rate_limited(acc.id)]
            if non_rate_limited:
                candidates = non_rate_limited
            else:
                # All eligible are rate limited -> pick the ones whose cooldown expires soonest
                candidates = sorted(
                    eligible,
                    key=lambda acc: self.get_cooldown_remaining(acc.id),
                )

            if strategy == "failover":
                return self._order_failover(candidates)
            return self._order_poll(candidates)

    def _order_poll(self, candidates: list[AccountRecord]) -> list[AccountRecord]:
        n = len(candidates)
        idx = self._cursor % n
        self._cursor = (idx + 1) % n
        return candidates[idx:] + candidates[:idx]

    def _order_failover(self, candidates: list[AccountRecord]) -> list[AccountRecord]:
        if not self._active_id:
            return list(candidates)
        active_idx = next((i for i, acc in enumerate(candidates) if acc.id == self._active_id), None)
        if active_idx is None:
            return list(candidates)
        return candidates[active_idx:] + candidates[:active_idx]

    def record_success(self, account_id: str, strategy: str = "poll") -> None:
        if strategy == "failover" and account_id:
            self._active_id = account_id

    def advance_active(
        self,
        account_id: str,
        candidates: list[AccountRecord] | None = None,
    ) -> None:
        """Advance the active failover pointer to the next eligible account."""
        if candidates is None:
            accounts = token_store.list_accounts()
            candidate_list = [
                acc
                for acc in accounts
                if self.eligible(acc) and not self.is_rate_limited(acc.id)
            ]
        else:
            candidate_list = candidates

        if not candidate_list:
            return
        idx = next((i for i, acc in enumerate(candidate_list) if acc.id == account_id), None)
        if idx is None:
            self._active_id = candidate_list[0].id
            return
        self._active_id = candidate_list[(idx + 1) % len(candidate_list)].id

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
