import time
import pytest
from qoder2oapi.models import AccountRecord
from qoder2oapi.quota import fetch_quota, parse_quota_data, remaining_credits, account_exceeded
import qoder2oapi.token_store as ts_mod


def test_quota_fixture_real_log():
    fixture = {
        "userId": "user-test-uuid",
        "userType": "personal_professional",
        "usageType": "credits",
        "totalUsagePercentage": 0.44,
        "isQuotaExceeded": False,
        "expiresAt": 1790007348608,
        "userQuota": {
            "total": 2000,
            "used": 2000,
            "remaining": 0,
            "percentage": 1,
            "unit": "credits",
        },
        "addOnQuota": {
            "total": 4000,
            "used": 585,
            "remaining": 3415,
            "percentage": 0.15,
            "unit": "credits",
        },
    }

    parsed = parse_quota_data(fixture)
    assert parsed["total_usage"] == 2585
    assert parsed["hard_limit"] == 6000
    assert parsed["is_quota_exceeded"] is False
    assert parsed["user_type"] == "personal_professional"
    assert parsed["expires_at"] == 1790007348608
    assert remaining_credits(parsed) == 3415
    assert account_exceeded(parsed) is False


@pytest.mark.asyncio
async def test_aggregate_quota_across_pool(monkeypatch):
    acc1 = AccountRecord(
        id="acc-1",
        kind="oauth",
        access_token="tok-1",
        user_id="u-1",
        machine_id="m-1",
        expires_at=1000,
    )
    acc2 = AccountRecord(
        id="acc-2",
        kind="pat",
        access_token="tok-2",
        user_id="u-2",
        machine_id="m-2",
        expires_at=int(time.time() * 1000) + 60 * 60 * 1000,
    )
    ts_mod.token_store.save_all([acc1, acc2])

    class MockResp:
        def __init__(self, token):
            self.status_code = 200
            self.token = token

        def json(self):
            if "tok-1" in self.token:
                return {
                    "data": {
                        "userType": "personal",
                        "expiresAt": 1000,
                        "userQuota": {"total": 100, "used": 40, "remaining": 60},
                        "addOnQuota": {"total": 50, "used": 10, "remaining": 40},
                    }
                }
            return {
                "data": {
                    "userType": "team",
                    "expiresAt": 2000,
                    "userQuota": {"total": 200, "used": 50, "remaining": 150},
                    "addOnQuota": {"total": 0, "used": 0, "remaining": 0},
                }
            }

    class MockClient:
        async def get(self, url, headers=None, **kwargs):
            auth = headers.get("Authorization", "")
            return MockResp(auth)

    from qoder2oapi import quota
    monkeypatch.setattr(quota, "get_http_client", lambda: MockClient())

    aggregated = await fetch_quota()
    assert aggregated["total_usage"] == (40 + 10) + (50 + 0)  # 100
    assert aggregated["hard_limit"] == (100 + 50) + (200 + 0)  # 350
    assert aggregated["user_quota"]["remaining"] == 60 + 150  # 210
    assert aggregated["user_type"] == "pool"
    assert aggregated["expires_at"] == 2000
    assert not aggregated["is_quota_exceeded"]
    assert len(aggregated["accounts"]) == 2


@pytest.mark.asyncio
async def test_pat_quota_401_forces_refresh_and_retries(monkeypatch):
    acc = AccountRecord(
        id="acc-pat",
        kind="pat",
        access_token="jt-old",
        refresh_token="jrt-old",
        pat="pt-secret",
        user_id="real-user-id",
        machine_id="m-pat",
        expires_at=int(time.time() * 1000) + 60 * 60 * 1000,
    )
    ts_mod.token_store.upsert(acc)
    requests = []

    class MockResp:
        def __init__(self, status_code, data=None):
            self.status_code = status_code
            self._data = data or {}

        def json(self):
            return self._data

    class MockClient:
        async def get(self, url, headers=None, **kwargs):
            token = headers.get("Authorization", "")
            requests.append(token)
            if token == "Bearer jt-old":
                return MockResp(401)
            return MockResp(
                200,
                {
                    "data": {
                        "userType": "personal",
                        "userQuota": {"total": 100, "used": 10, "remaining": 90},
                        "addOnQuota": {"total": 0, "used": 0, "remaining": 0},
                    }
                },
            )

    async def mock_ensure_fresh(account, force=False):
        if force:
            account.access_token = "jt-new"
            ts_mod.token_store.upsert(account)
        return account

    from qoder2oapi import quota

    monkeypatch.setattr(quota, "get_http_client", lambda: MockClient())
    monkeypatch.setattr(quota, "ensure_fresh", mock_ensure_fresh)

    result = await quota.fetch_quota_for_account(acc)
    assert result["user_quota"]["remaining"] == 90
    assert requests == ["Bearer jt-old", "Bearer jt-new"]
    saved = ts_mod.token_store.get(acc.id)
    assert saved is not None
    assert saved.skip_auth is False


@pytest.mark.asyncio
async def test_pat_quota_retry_500_does_not_mark_auth_failed(monkeypatch):
    acc = AccountRecord(
        id="acc-pat-500",
        kind="pat",
        access_token="jt-old",
        refresh_token="jrt-old",
        pat="pt-secret",
        user_id="real-user-id",
        machine_id="m-pat",
        expires_at=int(time.time() * 1000) + 60 * 60 * 1000,
    )
    ts_mod.token_store.upsert(acc)

    class MockResp:
        def __init__(self, status_code):
            self.status_code = status_code

    class MockClient:
        calls = 0

        async def get(self, *args, **kwargs):
            self.calls += 1
            return MockResp(401 if self.calls == 1 else 500)

    async def mock_ensure_fresh(account, force=False):
        if force:
            account.access_token = "jt-new"
        return account

    from qoder2oapi import quota

    client = MockClient()
    monkeypatch.setattr(quota, "get_http_client", lambda: client)
    monkeypatch.setattr(quota, "ensure_fresh", mock_ensure_fresh)

    result = await quota.fetch_quota_for_account(acc)
    assert result == {"error": "HTTP 500", "status_code": 500}
    saved = ts_mod.token_store.get(acc.id)
    assert saved is not None
    assert saved.skip_auth is False


def test_quota_dedicated_packages_parsing_and_remaining():
    fixture = {
        "userId": "user-test-uuid",
        "userType": "personal_professional",
        "isQuotaExceeded": True,
        "expiresAt": 1790007348608,
        "userQuota": {
            "total": 1000,
            "used": 1000,
            "remaining": 0,
        },
        "addOnQuota": {
            "total": 0,
            "used": 0,
            "remaining": 0,
        },
        "dedicatedResourcePackages": [
            {
                "packageName": "Qwen-Dedicated",
                "total": 500,
                "used": 100,
                "remaining": 400,
                "available": True,
                "status": "active",
            },
            {
                "packageName": "Expired-Pkg",
                "total": 200,
                "used": 200,
                "remaining": 0,
                "available": True,
                "status": "expired",
            },
        ],
    }

    parsed = parse_quota_data(fixture)
    assert len(parsed["dedicated_resource_packages"]) == 2
    # Normal user_quota is 0, add_on is 0, but active dedicated package has 400
    assert remaining_credits(parsed) == 400
    # Normal credits 0 + Qwen dedicated > 0 => account_exceeded is False!
    assert account_exceeded(parsed) is False
    assert parsed["total_usage"] == 1000 + 0 + (100 + 200)  # 1300
    assert parsed["hard_limit"] == 1000 + 0 + (500 + 200)  # 1700


@pytest.mark.asyncio
async def test_aggregate_quota_includes_dedicated_packages(monkeypatch):
    acc1 = AccountRecord(
        id="acc-pkg-1",
        kind="oauth",
        access_token="tok-pkg-1",
        user_id="u-pkg-1",
        machine_id="m-pkg-1",
        expires_at=1000,
    )
    ts_mod.token_store.save_all([acc1])

    class MockResp:
        def __init__(self):
            self.status_code = 200

        def json(self):
            return {
                "data": {
                    "userType": "personal",
                    "expiresAt": 1000,
                    "userQuota": {"total": 100, "used": 100, "remaining": 0},
                    "addOnQuota": {"total": 0, "used": 0, "remaining": 0},
                    "dedicated_resource_packages": [
                        {
                            "packageName": "Qwen-Package",
                            "total": 500,
                            "used": 50,
                            "remaining": 450,
                            "available": True,
                            "status": "normal",
                        }
                    ],
                }
            }

    class MockClient:
        async def get(self, url, headers=None, **kwargs):
            return MockResp()

    from qoder2oapi import quota
    monkeypatch.setattr(quota, "get_http_client", lambda: MockClient())

    aggregated = await fetch_quota()
    assert "dedicated_resource_packages" in aggregated
    assert len(aggregated["dedicated_resource_packages"]) == 1
    assert aggregated["dedicated_resource_packages"][0]["packageName"] == "Qwen-Package"
    # Even though user_quota and add_on_quota remaining are 0, total_remaining includes package remaining so is_quota_exceeded is False
    assert aggregated["user_quota"]["remaining"] == 0
    assert aggregated["add_on_quota"]["remaining"] == 0
    assert aggregated["is_quota_exceeded"] is False
    assert len(aggregated["accounts"]) == 1
    assert "dedicated_resource_packages" in aggregated["accounts"][0]["quota"]
