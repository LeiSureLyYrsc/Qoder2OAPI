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
        expires_at=2000,
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

