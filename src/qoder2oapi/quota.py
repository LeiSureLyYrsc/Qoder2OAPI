import asyncio
from typing import Any
from qoder2oapi.constants import QUOTA_URL
from qoder2oapi.http import get_http_client
from qoder2oapi.models import AccountRecord
from qoder2oapi.pool import pool
from qoder2oapi.refresh import ensure_fresh, needs_refresh
from qoder2oapi.token_store import token_store


def _is_package_active(pkg: dict[str, Any]) -> bool:
    if not isinstance(pkg, dict):
        return False
    # "available is not explicitly false"
    available = pkg.get("available")
    if available is False:
        return False
    # status is not an inactive/expired status
    status = str(pkg.get("status") or "").strip().lower()
    if status in ("inactive", "expired", "disabled", "invalid", "exhausted"):
        return False
    return True


def remaining_credits(parsed: dict[str, Any]) -> float:
    user_quota = parsed.get("user_quota") or {}
    addon_quota = parsed.get("add_on_quota") or {}
    user_rem = float(user_quota.get("remaining", 0) or 0)
    addon_rem = float(addon_quota.get("remaining", 0) or 0)

    pkg_rem = 0.0
    packages = parsed.get("dedicated_resource_packages") or []
    if isinstance(packages, list):
        for pkg in packages:
            if isinstance(pkg, dict) and _is_package_active(pkg):
                pkg_rem += float(pkg.get("remaining", 0) or 0)

    return user_rem + addon_rem + pkg_rem


def account_exceeded(parsed: dict[str, Any]) -> bool:
    rem = remaining_credits(parsed)
    if rem > 0:
        return False
    if parsed.get("is_quota_exceeded"):
        return True
    hard_limit = float(parsed.get("hard_limit", 0) or 0)
    total_usage = float(parsed.get("total_usage", 0) or 0)
    if rem == 0 and (hard_limit > 0 or total_usage > 0):
        return True
    return False


def _normalize_package(pkg: dict[str, Any]) -> dict[str, Any]:
    total = float(pkg.get("total", 0) or 0)
    used = float(pkg.get("used", 0) or 0)
    remaining = float(pkg.get("remaining", 0) if "remaining" in pkg else (total - used))
    normalized = dict(pkg)
    normalized["total"] = total
    normalized["used"] = used
    normalized["remaining"] = remaining
    return normalized


def parse_quota_data(data: dict[str, Any]) -> dict[str, Any]:
    user_quota = data.get("userQuota") or data.get("user_quota") or {}
    add_on_quota = data.get("addOnQuota") or data.get("add_on_quota") or {}

    user_used = float(user_quota.get("used", 0) or 0)
    addon_used = float(add_on_quota.get("used", 0) or 0)

    user_total = float(user_quota.get("total", 0) or 0)
    addon_total = float(add_on_quota.get("total", 0) or 0)

    raw_packages = (
        data.get("dedicatedResourcePackages")
        or data.get("dedicated_resource_packages")
        or []
    )
    dedicated_packages = []
    packages_used = 0.0
    packages_total = 0.0
    if isinstance(raw_packages, list):
        for item in raw_packages:
            if isinstance(item, dict):
                norm_pkg = _normalize_package(item)
                dedicated_packages.append(norm_pkg)
                packages_used += norm_pkg["used"]
                packages_total += norm_pkg["total"]

    total_usage = user_used + addon_used + packages_used
    hard_limit = user_total + addon_total + packages_total

    expires_at = data.get("expiresAt") or data.get("expires_at") or 0
    user_type = data.get("userType") or data.get("user_type") or "unknown"
    is_quota_exceeded = bool(data.get("isQuotaExceeded") or data.get("is_quota_exceeded", False))

    return {
        "object": "list",
        "total_usage": total_usage,
        "hard_limit": hard_limit,
        "user_quota": user_quota,
        "add_on_quota": add_on_quota,
        "dedicated_resource_packages": dedicated_packages,
        "expires_at": expires_at,
        "user_type": user_type,
        "is_quota_exceeded": is_quota_exceeded,
    }


async def fetch_quota_for_account(account: AccountRecord) -> dict[str, Any]:
    if needs_refresh(account):
        account = await ensure_fresh(account)
        if account.skip_auth:
            return {"error": account.last_error or "Authentication failed", "status_code": 401}
    client = get_http_client()
    headers = {
        "Authorization": f"Bearer {account.access_token}",
        "Accept": "application/json",
    }
    try:
        resp = await client.get(QUOTA_URL, headers=headers)
        if resp.status_code == 401:
            if needs_refresh(account):
                account = await ensure_fresh(account, force=True)
                if not account.skip_auth:
                    headers["Authorization"] = f"Bearer {account.access_token}"
                    resp = await client.get(QUOTA_URL, headers=headers)
                    if resp.status_code == 200:
                        res_json = resp.json()
                        data = res_json.get("data") if isinstance(res_json.get("data"), dict) else res_json
                        parsed = parse_quota_data(data)
                        account.quota_snapshot = parsed
                        if account_exceeded(parsed):
                            pool.mark_skip_quota(account.id, error="Quota exceeded")
                        else:
                            token_store.upsert(account)
                        return parsed
                    if resp.status_code not in (401, 403):
                        return {"error": f"HTTP {resp.status_code}", "status_code": resp.status_code}
            pool.mark_skip_auth(account.id, error=f"Quota fetch HTTP {resp.status_code}")
            return {"error": "Unauthorized", "status_code": resp.status_code}
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "status_code": resp.status_code}
        res_json = resp.json()
        data = res_json.get("data") if isinstance(res_json.get("data"), dict) else res_json
        parsed = parse_quota_data(data)
        account.quota_snapshot = parsed
        if account_exceeded(parsed):
            pool.mark_skip_quota(account.id, error="Quota exceeded")
        else:
            token_store.upsert(account)
        return parsed
    except Exception as e:
        return {"error": str(e)}


async def fetch_quota() -> dict[str, Any]:
    accounts = token_store.list_accounts()
    if not accounts:
        return {
            "object": "list",
            "total_usage": 0,
            "hard_limit": 0,
            "user_quota": {},
            "add_on_quota": {},
            "dedicated_resource_packages": [],
            "expires_at": 0,
            "user_type": "none",
            "is_quota_exceeded": True,
            "accounts": [],
        }

    targets = [acc for acc in accounts if acc.enabled and not acc.skip_auth]
    results = await asyncio.gather(*[fetch_quota_for_account(acc) for acc in targets])

    sum_user_total = 0.0
    sum_user_used = 0.0
    sum_user_remaining = 0.0
    sum_addon_total = 0.0
    sum_addon_used = 0.0
    sum_addon_remaining = 0.0
    aggregated_packages: list[dict[str, Any]] = []
    packages_remaining_total = 0.0
    max_expires_at = 0
    first_user_type = "unknown"

    account_reports = []
    for acc, res in zip(targets, results):
        report_item = {
            "id": acc.id,
            "user_id": acc.user_id,
            "email": acc.email,
            "name": acc.name,
            "kind": acc.kind,
            "quota": res,
            "flags": {
                "enabled": acc.enabled,
                "skip_quota": acc.skip_quota,
                "skip_auth": acc.skip_auth,
            },
        }
        account_reports.append(report_item)

        if "error" not in res:
            uq = res.get("user_quota") or {}
            aq = res.get("add_on_quota") or {}
            sum_user_total += float(uq.get("total", 0) or 0)
            sum_user_used += float(uq.get("used", 0) or 0)
            sum_user_remaining += float(uq.get("remaining", 0) or 0)
            sum_addon_total += float(aq.get("total", 0) or 0)
            sum_addon_used += float(aq.get("used", 0) or 0)
            sum_addon_remaining += float(aq.get("remaining", 0) or 0)

            pkgs = res.get("dedicated_resource_packages") or []
            if isinstance(pkgs, list):
                for pkg in pkgs:
                    if isinstance(pkg, dict):
                        aggregated_packages.append(pkg)
                        if _is_package_active(pkg):
                            packages_remaining_total += float(pkg.get("remaining", 0) or 0)

            max_expires_at = max(max_expires_at, int(res.get("expires_at", 0) or 0))
            if first_user_type == "unknown":
                first_user_type = res.get("user_type", "unknown")

    packages_total = sum(float(p.get("total", 0) or 0) for p in aggregated_packages)
    packages_used = sum(float(p.get("used", 0) or 0) for p in aggregated_packages)

    total_usage = sum_user_used + sum_addon_used + packages_used
    hard_limit = sum_user_total + sum_addon_total + packages_total
    total_remaining = sum_user_remaining + sum_addon_remaining + packages_remaining_total

    usable_count = len(pool.peek_usable())
    is_quota_exceeded = usable_count == 0 or total_remaining <= 0

    user_type = "pool" if len(accounts) > 1 else first_user_type

    return {
        "object": "list",
        "total_usage": total_usage,
        "hard_limit": hard_limit,
        "user_quota": {
            "total": sum_user_total,
            "used": sum_user_used,
            "remaining": sum_user_remaining,
        },
        "add_on_quota": {
            "total": sum_addon_total,
            "used": sum_addon_used,
            "remaining": sum_addon_remaining,
        },
        "dedicated_resource_packages": aggregated_packages,
        "expires_at": max_expires_at,
        "user_type": user_type,
        "is_quota_exceeded": is_quota_exceeded,
        "accounts": account_reports,
    }
