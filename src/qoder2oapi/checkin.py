import asyncio
from datetime import datetime, time, timedelta, timezone
from typing import Any

from qoder2oapi.constants import CAMPAIGNS_URL, CAMPAIGN_CLAIM_URL_TEMPLATE
from qoder2oapi.http import get_http_client
from qoder2oapi.models import AccountRecord
from qoder2oapi.pool import pool
from qoder2oapi.quota import fetch_quota_for_account
from qoder2oapi.refresh import ensure_fresh
from qoder2oapi.runtime_settings import runtime_settings, validate_checkin_time
from qoder2oapi.token_store import token_store

try:
    from zoneinfo import ZoneInfo
    SHANGHAI_TZ: Any = ZoneInfo("Asia/Shanghai")
except Exception:
    SHANGHAI_TZ = timezone(timedelta(hours=8))


def get_campaign_window_start(dt: datetime | None = None) -> datetime:
    """
    Official checkin window switches daily at 10:00 Asia/Shanghai (UTC+8).
    Returns the start datetime of the current 10:00 window.
    """
    if dt is None:
        dt = datetime.now(SHANGHAI_TZ)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=SHANGHAI_TZ)
    else:
        dt = dt.astimezone(SHANGHAI_TZ)

    if dt.time() >= time(10, 0):
        return datetime.combine(dt.date(), time(10, 0), tzinfo=SHANGHAI_TZ)
    else:
        return datetime.combine(dt.date() - timedelta(days=1), time(10, 0), tzinfo=SHANGHAI_TZ)


def is_target_campaign(campaign: dict[str, Any]) -> bool:
    """
    Filter checkin campaign strictly:
    - actionType == 'CLAIM_BENEFIT' (do not claim VIEW_DETAILS)
    - claimStatus == 'CLAIMABLE'
    - benefit.kind == 'CREDITS'
    - benefit.amount == 100
    - benefit.modelScope.modelSeries.key == 'ALL_MODELS' (fallback to campaign.modelScope)
    """
    if not isinstance(campaign, dict):
        return False
    if campaign.get("actionType") != "CLAIM_BENEFIT":
        return False
    if campaign.get("claimStatus") != "CLAIMABLE":
        return False

    benefit = campaign.get("benefit")
    if not isinstance(benefit, dict):
        return False
    if benefit.get("kind") != "CREDITS":
        return False
    try:
        amount = float(benefit.get("amount", 0) or 0)
        if amount != 100:
            return False
    except (TypeError, ValueError):
        return False

    model_scope = benefit.get("modelScope")
    if not isinstance(model_scope, dict):
        model_scope = campaign.get("modelScope")
    if not isinstance(model_scope, dict):
        return False

    model_series = model_scope.get("modelSeries")
    if not isinstance(model_series, dict):
        return False
    if model_series.get("key") != "ALL_MODELS":
        return False

    return True


def find_target_campaigns(campaigns_data: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    items: list[Any] = []
    if isinstance(campaigns_data, dict):
        c_list = campaigns_data.get("campaigns")
        if isinstance(c_list, list):
            items.extend(c_list)
        claimable = campaigns_data.get("claimable")
        if isinstance(claimable, list):
            items.extend(claimable)
        if not items and "data" in campaigns_data and isinstance(campaigns_data["data"], dict):
            return find_target_campaigns(campaigns_data["data"])
    elif isinstance(campaigns_data, list):
        items = campaigns_data

    seen_ids = set()
    matched = []
    for item in items:
        if isinstance(item, dict) and is_target_campaign(item):
            cid = str(item.get("campaignId") or item.get("id") or "")
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                matched.append(item)
            elif not cid:
                matched.append(item)
    return matched


async def checkin_account(account: AccountRecord) -> dict[str, Any]:
    """
    Execute checkin for a single account.
    Authenticates using Bearer access_token.
    If PAT account, calls ensure_fresh.
    If 401 occurs, for PAT forces refresh and retries.
    For OAuth or failed retry, marks auth via pool.mark_skip_auth (consistent with quota).
    """
    now_ts = int(datetime.now(SHANGHAI_TZ).timestamp() * 1000)

    if account.kind == "pat":
        account = await ensure_fresh(account)
        if account.skip_auth:
            res = {
                "status": "failed",
                "error": account.last_error or "PAT authentication failed",
                "status_code": 401,
            }
            _save_account_checkin_result(account, res, now_ts)
            return res

    client = get_http_client()
    headers = {
        "Authorization": f"Bearer {account.access_token}",
        "Accept": "application/json",
    }

    try:
        resp = await client.get(CAMPAIGNS_URL, headers=headers)
        if resp.status_code == 401:
            if account.kind == "pat":
                account = await ensure_fresh(account, force=True)
                if not account.skip_auth:
                    headers["Authorization"] = f"Bearer {account.access_token}"
                    resp = await client.get(CAMPAIGNS_URL, headers=headers)
            if resp.status_code == 401:
                pool.mark_skip_auth(account.id, error=f"Checkin fetch HTTP {resp.status_code}")
                res = {
                    "status": "failed",
                    "error": "Unauthorized",
                    "status_code": resp.status_code,
                }
                _save_account_checkin_result(account, res, now_ts)
                return res

        if resp.status_code != 200:
            res = {
                "status": "failed",
                "error": f"HTTP {resp.status_code}",
                "status_code": resp.status_code,
            }
            _save_account_checkin_result(account, res, now_ts)
            return res

        res_json = resp.json()
        campaigns_payload = res_json.get("data") if isinstance(res_json.get("data"), dict) else res_json
        matched_campaigns = find_target_campaigns(campaigns_payload)

        if not matched_campaigns:
            res = {
                "status": "already",
                "message": "No claimable checkin campaign found (already claimed or none available)",
                "claimed_count": 0,
            }
            _save_account_checkin_result(account, res, now_ts)
            return res

        claimed_any = False
        replayed_any = False
        for camp in matched_campaigns:
            cid = camp.get("campaignId") or camp.get("id")
            claim_url = CAMPAIGN_CLAIM_URL_TEMPLATE.format(campaign_id=cid)
            claim_resp = await client.post(claim_url, headers=headers, json={})
            if claim_resp.status_code == 401 and account.kind == "pat":
                account = await ensure_fresh(account, force=True)
                if not account.skip_auth:
                    headers["Authorization"] = f"Bearer {account.access_token}"
                    claim_resp = await client.post(claim_url, headers=headers, json={})

            if claim_resp.status_code == 401:
                pool.mark_skip_auth(account.id, error=f"Checkin claim HTTP {claim_resp.status_code}")
                res = {
                    "status": "failed",
                    "error": "Unauthorized on claim",
                    "status_code": claim_resp.status_code,
                }
                _save_account_checkin_result(account, res, now_ts)
                return res

            if claim_resp.status_code != 200:
                res = {
                    "status": "failed",
                    "error": f"Claim HTTP {claim_resp.status_code}",
                    "status_code": claim_resp.status_code,
                }
                _save_account_checkin_result(account, res, now_ts)
                return res

            claim_data = claim_resp.json()
            if isinstance(claim_data.get("data"), dict):
                claim_data = claim_data["data"]
            status = claim_data.get("status")
            replayed = bool(claim_data.get("replayed", False))

            if replayed:
                replayed_any = True
            elif status == "CLAIMED":
                claimed_any = True
            else:
                res = {
                    "status": "failed",
                    "error": f"Claim status: {status or 'unknown'}",
                }
                _save_account_checkin_result(account, res, now_ts)
                return res

        quota_snapshot = {}
        if claimed_any:
            try:
                quota_res = await fetch_quota_for_account(account)
                if isinstance(quota_res, dict) and "error" not in quota_res:
                    quota_snapshot = quota_res
            except Exception:
                pass

        if claimed_any:
            res = {
                "status": "success",
                "message": "Checkin completed",
                "claimed_count": len(matched_campaigns),
                "replayed": False,
            }
        else:
            res = {
                "status": "already",
                "message": "Already claimed",
                "claimed_count": 0,
                "replayed": True,
            }

        _save_account_checkin_result(account, res, now_ts, quota_snapshot=quota_snapshot if quota_snapshot else None)
        return res

    except Exception as e:
        res = {"status": "failed", "error": str(e)}
        _save_account_checkin_result(account, res, now_ts)
        return res


def _save_account_checkin_result(
    account: AccountRecord,
    result: dict[str, Any],
    ts: int,
    quota_snapshot: dict[str, Any] | None = None,
) -> None:
    fresh_acc = token_store.get(account.id) or account
    fresh_acc.checkin = {
        "status": result.get("status", "unknown"),
        "last_at": ts,
        "result": result,
        "error": result.get("error", ""),
    }
    if quota_snapshot:
        fresh_acc.quota_snapshot = quota_snapshot
    token_store.upsert(fresh_acc)


class CheckinScheduler:
    def __init__(self) -> None:
        self.last_run_at: int | None = None
        self.last_run_summary: dict[str, Any] | None = None
        self.last_run_window_key: str | None = None  # e.g. "2026-09-20_10"
        self._task: asyncio.Task[Any] | None = None
        self._running_lock = asyncio.Lock()

    def get_status_overview(self) -> dict[str, Any]:
        next_run_ts = self.compute_next_run_timestamp()
        return {
            "auto_checkin": runtime_settings.auto_checkin,
            "checkin_time": runtime_settings.checkin_time,
            "next_run_at": next_run_ts,
            "last_run_at": self.last_run_at,
            "last_run_summary": self.last_run_summary,
            "timezone": "Asia/Shanghai",
        }

    def compute_next_run_timestamp(self, now: datetime | None = None) -> int | None:
        if not runtime_settings.auto_checkin:
            return None
        time_str = runtime_settings.checkin_time
        if not validate_checkin_time(time_str):
            time_str = "10:05"
        hh, mm = map(int, time_str.split(":"))

        if now is None:
            now = datetime.now(SHANGHAI_TZ)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=SHANGHAI_TZ)
        else:
            now = now.astimezone(SHANGHAI_TZ)

        scheduled_today = datetime.combine(now.date(), time(hh, mm), tzinfo=SHANGHAI_TZ)
        if now <= scheduled_today:
            next_run = scheduled_today
        else:
            next_run = scheduled_today + timedelta(days=1)

        return int(next_run.timestamp() * 1000)

    async def run_checkin_all(self, trigger_source: str = "manual") -> dict[str, Any]:
        async with self._running_lock:
            accounts = token_store.list_accounts()
            eligible = [acc for acc in accounts if acc.enabled and not acc.skip_auth]
            now_shanghai = datetime.now(SHANGHAI_TZ)
            window_start = get_campaign_window_start(now_shanghai)
            window_start_ms = int(window_start.timestamp() * 1000)
            window_key = window_start.strftime("%Y-%m-%d_%H")

            self.last_run_at = int(now_shanghai.timestamp() * 1000)
            self.last_run_window_key = window_key

            if trigger_source == "manual":
                targets = eligible
            else:
                targets = [
                    acc for acc in eligible
                    if int((acc.checkin or {}).get("last_at") or 0) < window_start_ms
                ]

            if not targets:
                summary = {
                    "total": len(accounts),
                    "target_count": 0,
                    "success": 0,
                    "failed": 0,
                    "already": 0,
                    "source": trigger_source,
                    "message": "All accounts already checked in current window" if trigger_source != "manual" else "No eligible accounts for checkin",
                    "accounts": [],
                }
                self.last_run_summary = summary
                return summary

            results = await asyncio.gather(*[checkin_account(acc) for acc in targets])

            success_cnt = 0
            failed_cnt = 0
            already_cnt = 0
            account_summaries = []

            for acc, res in zip(targets, results):
                st = res.get("status")
                if st == "success":
                    success_cnt += 1
                elif st == "already":
                    already_cnt += 1
                else:
                    failed_cnt += 1

                account_summaries.append({
                    "id": acc.id,
                    "name": acc.name or acc.email or acc.user_id,
                    "status": st,
                    "result": res,
                })

            summary = {
                "total": len(accounts),
                "target_count": len(targets),
                "success": success_cnt,
                "failed": failed_cnt,
                "already": already_cnt,
                "source": trigger_source,
                "accounts": account_summaries,
            }
            self.last_run_summary = summary
            return summary

    async def _loop(self) -> None:
        """
        Background scheduler:
        - On startup: If auto_checkin is True and current time >= checkin_time today,
          and there are eligible accounts that haven't attempted checkin in current 10:00 window,
          run catch-up checkin.
        - Every ~30s checks if it's time to run for today's window.
        """
        now = datetime.now(SHANGHAI_TZ)
        time_str = runtime_settings.checkin_time
        if validate_checkin_time(time_str):
            hh, mm = map(int, time_str.split(":"))
            scheduled_today = datetime.combine(now.date(), time(hh, mm), tzinfo=SHANGHAI_TZ)
            if runtime_settings.auto_checkin and now >= scheduled_today:
                try:
                    await self.run_checkin_all(trigger_source="startup_catchup")
                except Exception:
                    pass

        while True:
            try:
                await asyncio.sleep(30)
                if not runtime_settings.auto_checkin:
                    continue

                now = datetime.now(SHANGHAI_TZ)
                time_str = runtime_settings.checkin_time
                if not validate_checkin_time(time_str):
                    continue

                hh, mm = map(int, time_str.split(":"))
                scheduled_today = datetime.combine(now.date(), time(hh, mm), tzinfo=SHANGHAI_TZ)
                window_start = get_campaign_window_start(now)
                window_key = window_start.strftime("%Y-%m-%d_%H")

                if now >= scheduled_today:
                    if self.last_run_window_key != window_key:
                        await self.run_checkin_all(trigger_source="scheduler")
                    else:
                        window_start_ms = int(window_start.timestamp() * 1000)
                        accounts = token_store.list_accounts()
                        has_pending = any(
                            acc.enabled and not acc.skip_auth and int((acc.checkin or {}).get("last_at") or 0) < window_start_ms
                            for acc in accounts
                        )
                        if has_pending:
                            await self.run_checkin_all(trigger_source="scheduler")

            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(10)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()


checkin_scheduler = CheckinScheduler()
