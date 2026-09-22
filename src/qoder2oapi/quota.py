import asyncio
from typing import Any
from qoder2oapi.constants import QUOTA_URL
from qoder2oapi.http import get_http_client
from qoder2oapi.models import AccountRecord
from qoder2oapi.pool import pool
from qoder2oapi.refresh import ensure_fresh, needs_refresh
from qoder2oapi.token_store import token_store


_GENERIC_TITLES = {
    "专属资源包",
    "dedicated resource package",
    "dedicated package",
    "package",
}


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


def _extract_package_title(pkg: dict[str, Any]) -> str | None:
    if not isinstance(pkg, dict):
        return None
    # 1) displayLabels array
    labels = pkg.get("displayLabels") or pkg.get("display_labels")
    if isinstance(labels, list):
        for label in labels:
            if isinstance(label, dict) and label.get("dimension") == "title":
                i18n = label.get("valueI18n") or label.get("value_i18n") or {}
                candidate = i18n.get("zh-CN") or i18n.get("zh_CN") or label.get("value")
                if isinstance(candidate, str):
                    candidate = candidate.strip()
                    if candidate and candidate.lower() not in _GENERIC_TITLES:
                        return candidate
    # 2) displayLabels object
    if isinstance(labels, dict):
        for key in ("zh-CN", "title", "value"):
            val = labels.get(key)
            if isinstance(val, str):
                val = val.strip()
                if val and val.lower() not in _GENERIC_TITLES:
                    return val
    # 3) direct fields: prefer title/description over packageName (matches frontend)
    for key in ("title", "description", "name", "packageName"):
        val = pkg.get(key)
        if isinstance(val, str):
            val = val.strip()
            if val and val.lower() not in _GENERIC_TITLES:
                return val
    return None


def _extract_plan_name(pkg: dict[str, Any]) -> str | None:
    if not isinstance(pkg, dict):
        return None
    for key in ("packageName", "name", "plan", "userType", "user_type"):
        val = pkg.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


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
    if rem <= 0 and (hard_limit > 0 or total_usage > 0):
        return True
    return False


def _quota_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    return {
        "total": float(bucket.get("total", 0) or 0),
        "used": float(bucket.get("used", 0) or 0),
        "remaining": float(bucket.get("remaining", 0) or 0),
        "unit": bucket.get("unit") or "credits",
    }


def _normalize_package(pkg: dict[str, Any]) -> dict[str, Any]:
    total = float(pkg.get("total", 0) or 0)
    used = float(pkg.get("used", 0) or 0)
    remaining = float(pkg.get("remaining", 0) if "remaining" in pkg else (total - used))
    title = _extract_package_title(pkg)
    plan_name = _extract_plan_name(pkg)
    recognized = title is not None
    normalized = dict(pkg)
    normalized["total"] = total
    normalized["used"] = used
    normalized["remaining"] = remaining
    normalized["unit"] = pkg.get("unit") or "credits"
    normalized["title"] = title
    normalized["plan_name"] = plan_name
    normalized["recognized"] = recognized
    normalized["available"] = _is_package_active(pkg)
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

    parsed = {
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
    parsed["remaining"] = remaining_credits(parsed)
    return parsed


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


def _build_account_report(acc: AccountRecord, res: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": acc.id,
        "user_id": acc.user_id,
        "email": acc.email,
        "name": acc.name,
        "kind": acc.kind,
        "client": acc.client,
        "user_type": res.get("user_type") if "error" not in res else "unknown",
        "expires_at": res.get("expires_at") if "error" not in res else 0,
        "quota": res,
        "flags": {
            "enabled": acc.enabled,
            "skip_quota": acc.skip_quota,
            "skip_auth": acc.skip_auth,
        },
    }


def _aggregate_quota(results: list[dict[str, Any]], targets: list[AccountRecord]) -> dict[str, Any]:
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
        account_reports.append(_build_account_report(acc, res))

        if "error" in res:
            continue

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

    user_type = "pool" if len(targets) > 1 else first_user_type

    return {
        "object": "list",
        "total_usage": total_usage,
        "hard_limit": hard_limit,
        "user_quota": {
            "total": sum_user_total,
            "used": sum_user_used,
            "remaining": sum_user_remaining,
            "unit": "credits",
        },
        "add_on_quota": {
            "total": sum_addon_total,
            "used": sum_addon_used,
            "remaining": sum_addon_remaining,
            "unit": "credits",
        },
        "dedicated_resource_packages": aggregated_packages,
        "expires_at": max_expires_at,
        "user_type": user_type,
        "is_quota_exceeded": is_quota_exceeded,
        "accounts": account_reports,
    }


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
    if not targets:
        return _aggregate_quota([], [])

    results = await asyncio.gather(*[fetch_quota_for_account(acc) for acc in targets])
    return _aggregate_quota(results, targets)


def _build_dedicated_entry(pkg: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": pkg.get("title"),
        "plan_name": pkg.get("plan_name"),
        "recognized": pkg.get("recognized", pkg.get("title") is not None),
        "total": float(pkg.get("total", 0) or 0),
        "used": float(pkg.get("used", 0) or 0),
        "remaining": float(pkg.get("remaining", 0) or 0),
        "unit": pkg.get("unit") or "credits",
        "available": pkg.get("available", _is_package_active(pkg)),
    }


def _build_account_credit_entry(acc_report: dict[str, Any]) -> dict[str, Any]:
    res = acc_report.get("quota") or {}
    general = _quota_bucket(res.get("user_quota") or {})
    addon = _quota_bucket(res.get("add_on_quota") or {})
    dedicated = [_build_dedicated_entry(pkg) for pkg in (res.get("dedicated_resource_packages") or []) if isinstance(pkg, dict)]
    return {
        "id": acc_report["id"],
        "user_id": acc_report["user_id"],
        "email": acc_report["email"],
        "name": acc_report["name"],
        "kind": acc_report["kind"],
        "client": acc_report["client"],
        "user_type": acc_report.get("user_type", "unknown"),
        "expires_at": acc_report.get("expires_at", 0),
        "general": general,
        "addon": addon,
        "dedicated": dedicated,
        "remaining": remaining_credits(res) if "error" not in res else 0.0,
        "flags": acc_report["flags"],
        "error": res.get("error") if "error" in res else None,
    }


async def fetch_credits() -> dict[str, Any]:
    quota = await fetch_quota()

    general = _quota_bucket(quota.get("user_quota") or {})
    addon = _quota_bucket(quota.get("add_on_quota") or {})
    dedicated = [_build_dedicated_entry(pkg) for pkg in (quota.get("dedicated_resource_packages") or []) if isinstance(pkg, dict)]

    accounts = [_build_account_credit_entry(report) for report in (quota.get("accounts") or [])]

    return {
        "object": "billing_credits",
        "general": general,
        "addon": addon,
        "dedicated": dedicated,
        "expires_at": quota.get("expires_at", 0),
        "user_type": quota.get("user_type", "unknown"),
        "is_quota_exceeded": quota.get("is_quota_exceeded", True),
        "total_usage": quota.get("total_usage", 0.0),
        "hard_limit": quota.get("hard_limit", 0.0),
        "accounts": accounts,
    }
