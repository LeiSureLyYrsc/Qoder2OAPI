from datetime import datetime, time, timedelta
from typing import Any
import httpx
import pytest

from qoder2oapi.app import create_app
from qoder2oapi.checkin import (
    SHANGHAI_TZ,
    build_checkin_headers,
    checkin_account,
    checkin_scheduler,
    find_target_campaigns,
    get_campaign_window_start,
    is_target_campaign,
)
from qoder2oapi.models import AccountRecord
from qoder2oapi.pool import pool
import qoder2oapi.runtime_settings as rs_mod
from qoder2oapi.runtime_settings import validate_checkin_time
import qoder2oapi.token_store as ts_mod


class DummyResponse:
    def __init__(self, status_code: int, json_data: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self) -> Any:
        if self._json_data is not None:
            return self._json_data
        raise ValueError("No JSON")


def test_build_checkin_headers():
    acc = AccountRecord(
        id="acc-1",
        kind="oauth",
        access_token="test-access-token",
        user_id="u1",
        machine_id="m1",
    )
    headers = build_checkin_headers(acc)
    assert headers["Authorization"] == "Bearer test-access-token"
    assert headers["Cosy-ClientType"] == "10"
    assert headers["Cosy-Version"] == "0.3.3"
    assert headers["Cosy-MachineOS"] == "windows"
    assert headers["User-Agent"] == "Qoder"
    assert headers["Accept"] == "application/json"


def test_is_target_campaign():
    valid = {
        "actionType": "CLAIM_BENEFIT",
        "claimStatus": "CLAIMABLE",
        "benefit": {"kind": "CREDITS", "amount": 100},
    }
    assert is_target_campaign(valid) is True

    # Not claim benefit
    assert is_target_campaign({**valid, "actionType": "VIEW_DETAILS"}) is False
    # Not claimable
    assert is_target_campaign({**valid, "claimStatus": "CLAIMED"}) is False
    # Not credits
    assert is_target_campaign({**valid, "benefit": {"kind": "TOKENS", "amount": 100}}) is False
    # Benefit missing or not dict
    assert is_target_campaign({"actionType": "CLAIM_BENEFIT", "claimStatus": "CLAIMABLE"}) is False
    assert is_target_campaign({"actionType": "CLAIM_BENEFIT", "claimStatus": "CLAIMABLE", "benefit": "none"}) is False
    # Invalid input
    assert is_target_campaign(None) is False
    assert is_target_campaign("string") is False


def test_find_target_campaigns():
    data = {
        "campaigns": [
            {
                "campaignId": "camp-1",
                "actionType": "CLAIM_BENEFIT",
                "claimStatus": "CLAIMABLE",
                "benefit": {"kind": "CREDITS", "amount": 100},
            },
            {
                "campaignId": "camp-1",  # duplicate
                "actionType": "CLAIM_BENEFIT",
                "claimStatus": "CLAIMABLE",
                "benefit": {"kind": "CREDITS", "amount": 100},
            },
            {
                "campaignId": "camp-2",
                "actionType": "VIEW_DETAILS",
                "claimStatus": "CLAIMABLE",
                "benefit": {"kind": "CREDITS", "amount": 100},
            },
        ]
    }
    targets = find_target_campaigns(data)
    assert len(targets) == 1
    assert targets[0]["campaignId"] == "camp-1"


def test_get_campaign_window_start():
    # Before 10:00 (e.g. 09:30 Shanghai)
    dt_early = datetime(2026, 10, 15, 9, 30, tzinfo=SHANGHAI_TZ)
    w_early = get_campaign_window_start(dt_early)
    assert w_early.date() == dt_early.date() - timedelta(days=1)
    assert w_early.time() == time(10, 0)

    # At or after 10:00 (e.g. 10:05 Shanghai)
    dt_late = datetime(2026, 10, 15, 10, 5, tzinfo=SHANGHAI_TZ)
    w_late = get_campaign_window_start(dt_late)
    assert w_late.date() == dt_late.date()
    assert w_late.time() == time(10, 0)


def test_validate_checkin_time():
    assert validate_checkin_time("10:05") is True
    assert validate_checkin_time("00:00") is True
    assert validate_checkin_time("23:59") is True
    assert validate_checkin_time("24:00") is False
    assert validate_checkin_time("10:5") is False
    assert validate_checkin_time("invalid") is False
    assert validate_checkin_time(None) is False


@pytest.mark.asyncio
async def test_checkin_account_success(monkeypatch):
    acc = AccountRecord(
        id="acc-success",
        kind="oauth",
        access_token="valid-token",
        user_id="u-succ",
        machine_id="m1",
    )
    ts_mod.token_store.upsert(acc)

    requested_urls = []
    sent_headers = []

    class MockClient:
        async def get(self, url, headers=None):
            requested_urls.append(url)
            sent_headers.append(headers)
            return DummyResponse(
                200,
                {
                    "campaigns": [
                        {
                            "campaignId": "c1",
                            "actionType": "CLAIM_BENEFIT",
                            "claimStatus": "CLAIMABLE",
                            "benefit": {"kind": "CREDITS", "amount": 100},
                        }
                    ]
                },
            )

        async def post(self, url, headers=None, json=None):
            requested_urls.append(url)
            sent_headers.append(headers)
            return DummyResponse(200, {"status": "CLAIMED", "benefit": {"amount": 100}})

    from qoder2oapi import checkin
    monkeypatch.setattr(checkin, "get_http_client", lambda: MockClient())

    async def mock_quota(a):
        return {"total_usage": 0, "hard_limit": 100}

    monkeypatch.setattr(checkin, "fetch_quota_for_account", mock_quota)

    res = await checkin_account(acc)
    assert res["status"] == "success"
    assert res["claimed_count"] == 1
    assert res["credits"] == 100

    # Verify simulated desktop headers were passed
    assert len(sent_headers) == 2
    for h in sent_headers:
        assert h["Cosy-ClientType"] == "10"
        assert h["User-Agent"] == "Qoder"

    saved = ts_mod.token_store.get("acc-success")
    assert saved is not None
    assert saved.checkin["status"] == "success"
    assert saved.quota_snapshot == {"total_usage": 0, "hard_limit": 100}


@pytest.mark.asyncio
async def test_checkin_account_already_claimed(monkeypatch):
    acc = AccountRecord(
        id="acc-already",
        kind="oauth",
        access_token="tok",
        user_id="u-alr",
        machine_id="m1",
    )
    ts_mod.token_store.upsert(acc)

    class MockClient:
        async def get(self, url, headers=None):
            return DummyResponse(200, {"campaigns": []})

    from qoder2oapi import checkin
    monkeypatch.setattr(checkin, "get_http_client", lambda: MockClient())

    res = await checkin_account(acc)
    assert res["status"] == "already"
    assert res["claimed_count"] == 0

    saved = ts_mod.token_store.get("acc-already")
    assert saved is not None
    assert saved.checkin["status"] == "already"


@pytest.mark.asyncio
async def test_checkin_account_401_pat_refresh_success(monkeypatch):
    acc = AccountRecord(
        id="acc-pat-401",
        kind="pat",
        pat="my-pat",
        access_token="old-token",
        user_id="u-pat",
        machine_id="m1",
    )
    ts_mod.token_store.upsert(acc)

    calls = 0

    class MockClient:
        async def get(self, url, headers=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                return DummyResponse(401, {"error": "unauthorized"})
            return DummyResponse(
                200,
                {
                    "campaigns": [
                        {
                            "campaignId": "c-refreshed",
                            "actionType": "CLAIM_BENEFIT",
                            "claimStatus": "CLAIMABLE",
                            "benefit": {"kind": "CREDITS", "amount": 100},
                        }
                    ]
                },
            )

        async def post(self, url, headers=None, json=None):
            return DummyResponse(200, {"status": "CLAIMED", "benefit": {"amount": 100}})

    from qoder2oapi import checkin
    monkeypatch.setattr(checkin, "get_http_client", lambda: MockClient())

    async def mock_refresh(a, force=False):
        a.access_token = "new-fresh-token"
        return a

    monkeypatch.setattr(checkin, "ensure_fresh", mock_refresh)

    res = await checkin_account(acc)
    assert res["status"] == "success"
    assert calls == 2


@pytest.mark.asyncio
async def test_checkin_account_401_oauth_marks_skip_auth(monkeypatch):
    acc = AccountRecord(
        id="acc-oauth-401",
        kind="oauth",
        access_token="bad-token",
        user_id="u-oauth",
        machine_id="m1",
    )
    ts_mod.token_store.upsert(acc)

    class MockClient:
        async def get(self, url, headers=None):
            return DummyResponse(401, {"error": "unauthorized"})

    from qoder2oapi import checkin
    monkeypatch.setattr(checkin, "get_http_client", lambda: MockClient())

    res = await checkin_account(acc)
    assert res["status"] == "failed"
    assert res["status_code"] == 401

    saved = ts_mod.token_store.get("acc-oauth-401")
    assert saved.skip_auth is True


@pytest.mark.asyncio
async def test_scheduler_run_all(monkeypatch):
    acc1 = AccountRecord(
        id="acc-sched-1",
        kind="oauth",
        access_token="tok1",
        user_id="u-sched-1",
        machine_id="m1",
        checkin={"last_at": 0},
    )
    acc2 = AccountRecord(
        id="acc-sched-2",
        kind="oauth",
        access_token="tok2",
        user_id="u-sched-2",
        machine_id="m2",
        checkin={"last_at": int(datetime.now(SHANGHAI_TZ).timestamp() * 1000)},  # already in current window
    )
    ts_mod.token_store.upsert(acc1)
    ts_mod.token_store.upsert(acc2)

    called_accounts = []

    async def mock_checkin(acc):
        called_accounts.append(acc.id)
        return {"status": "success", "claimed_count": 1}

    from qoder2oapi import checkin
    monkeypatch.setattr(checkin, "checkin_account", mock_checkin)

    # 1. Scheduler trigger should skip acc2 since it already ran in current window
    sched_summary = await checkin_scheduler.run_checkin_all(trigger_source="scheduler")
    assert sched_summary["target_count"] == 1
    assert called_accounts == ["acc-sched-1"]

    called_accounts.clear()

    # 2. Manual trigger runs for ALL eligible accounts
    man_summary = await checkin_scheduler.run_checkin_all(trigger_source="manual")
    assert man_summary["target_count"] == 2
    assert sorted(called_accounts) == ["acc-sched-1", "acc-sched-2"]


def test_scheduler_compute_next_run():
    rs_mod.runtime_settings.auto_checkin = False
    assert checkin_scheduler.compute_next_run_timestamp() is None

    rs_mod.runtime_settings.auto_checkin = True
    rs_mod.runtime_settings.checkin_time = "10:05"
    now_early = datetime(2026, 10, 15, 8, 0, tzinfo=SHANGHAI_TZ)
    next_ts = checkin_scheduler.compute_next_run_timestamp(now_early)
    assert next_ts is not None
    next_dt = datetime.fromtimestamp(next_ts / 1000, tz=SHANGHAI_TZ)
    assert next_dt.date() == now_early.date()
    assert next_dt.time() == time(10, 5)

    now_late = datetime(2026, 10, 15, 11, 0, tzinfo=SHANGHAI_TZ)
    next_ts_late = checkin_scheduler.compute_next_run_timestamp(now_late)
    next_dt_late = datetime.fromtimestamp(next_ts_late / 1000, tz=SHANGHAI_TZ)
    assert next_dt_late.date() == now_late.date() + timedelta(days=1)
    assert next_dt_late.time() == time(10, 5)


@pytest.fixture
def test_client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_admin_checkin_routes(test_client, monkeypatch):
    headers = {"Authorization": "Bearer test-secret-key-1234"}

    acc = AccountRecord(
        id="acc-route-1",
        kind="oauth",
        access_token="tok",
        user_id="u-route",
        machine_id="m1",
    )
    ts_mod.token_store.upsert(acc)

    async def mock_checkin(a):
        return {"status": "success", "claimed_count": 1}

    from qoder2oapi import checkin
    monkeypatch.setattr(checkin, "checkin_account", mock_checkin)
    from qoder2oapi.routes import admin as admin_mod
    monkeypatch.setattr(admin_mod, "checkin_account", mock_checkin)

    # 1. GET /api/admin/checkin/status
    res = await test_client.get("/api/admin/checkin/status", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "auto_checkin" in data
    assert "checkin_time" in data

    # 2. POST /api/admin/checkin/run
    res = await test_client.post("/api/admin/checkin/run", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["source"] == "manual"
    assert data["target_count"] == 1

    # 3. POST /api/admin/accounts/{id}/checkin
    res = await test_client.post("/api/admin/accounts/acc-route-1/checkin", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["success"] is True

    # 4. PATCH /api/admin/settings (validate checkin settings)
    # Invalid time format -> 400
    res = await test_client.patch(
        "/api/admin/settings",
        headers=headers,
        json={"checkin_time": "invalid_time"},
    )
    assert res.status_code == 400

    # Valid time format -> 200
    res = await test_client.patch(
        "/api/admin/settings",
        headers=headers,
        json={"auto_checkin": False, "checkin_time": "11:30"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["auto_checkin"] is False
    assert data["checkin_time"] == "11:30"


def test_token_store_checkin_persistence():
    acc = AccountRecord(
        id="acc-persist",
        kind="oauth",
        access_token="tok",
        user_id="u-persist",
        machine_id="m1",
        checkin={"status": "success", "last_at": 123456789},
    )
    ts_mod.token_store.upsert(acc)

    # Reload from store
    reloaded = ts_mod.token_store.get("acc-persist")
    assert reloaded is not None
    assert reloaded.checkin == {"status": "success", "last_at": 123456789}
