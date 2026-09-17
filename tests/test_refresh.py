import time
import pytest
from qoder2oapi.models import AccountRecord
from qoder2oapi.refresh import ensure_fresh, exchange_pat
import qoder2oapi.token_store as ts_mod


@pytest.mark.asyncio
async def test_kind_oauth_ensure_fresh_does_not_http(monkeypatch):
    called = False

    class MockClient:
        async def post(self, *args, **kwargs):
            nonlocal called
            called = True
            raise RuntimeError("Should not be called")

    from qoder2oapi import refresh
    monkeypatch.setattr(refresh, "get_http_client", lambda: MockClient())

    acc = AccountRecord(
        id="acc-oauth",
        kind="oauth",
        access_token="tok",
        refresh_token="ref",
        user_id="u1",
        machine_id="m1",
        expires_at=0,  # even if expired, oauth shouldn't refresh
    )
    res = await ensure_fresh(acc)
    assert not called
    assert res.access_token == "tok"


@pytest.mark.asyncio
async def test_pat_not_expired_skips_http(monkeypatch):
    called = False

    class MockClient:
        async def post(self, *args, **kwargs):
            nonlocal called
            called = True
            raise RuntimeError("Should not be called")

    from qoder2oapi import refresh
    monkeypatch.setattr(refresh, "get_http_client", lambda: MockClient())

    future_exp = int(time.time() * 1000) + 10 * 60 * 1000  # +10 minutes
    acc = AccountRecord(
        id="acc-pat",
        kind="pat",
        access_token="jt-tok",
        refresh_token="rt-tok",
        pat="pt-secret",
        user_id="u1",
        machine_id="m1",
        expires_at=future_exp,
    )
    res = await ensure_fresh(acc)
    assert not called
    assert res.access_token == "jt-tok"


@pytest.mark.asyncio
async def test_fresh_pat_clears_stale_skip_auth_without_http(monkeypatch):
    called = False

    class MockClient:
        async def post(self, *args, **kwargs):
            nonlocal called
            called = True
            raise RuntimeError("Should not be called")

    from qoder2oapi import refresh

    monkeypatch.setattr(refresh, "get_http_client", lambda: MockClient())
    account = AccountRecord(
        id="stale-skip",
        kind="pat",
        access_token="jt-valid",
        refresh_token="jrt-valid",
        pat="pt-secret",
        user_id="real-user-id",
        machine_id="m1",
        expires_at=int(time.time() * 1000) + 60 * 60 * 1000,
        skip_auth=True,
        last_error="old transient failure",
    )
    ts_mod.token_store.upsert(account)

    result = await ensure_fresh(account)
    assert called is False
    assert result.skip_auth is False
    assert result.last_error == ""
    saved = ts_mod.token_store.get(account.id)
    assert saved is not None and saved.skip_auth is False


@pytest.mark.asyncio
async def test_exchange_pat_reads_userinfo_id_and_millisecond_ttl(monkeypatch):
    now_ms = int(time.time() * 1000)

    class MockResp:
        status_code = 200
        text = ""

        def json(self):
            return {
                "token": "jt-token",
                "refresh_token": "jrt-token",
                "expires_in": 86_400_000,
            }

    class MockClient:
        async def post(self, *args, **kwargs):
            return MockResp()

    from qoder2oapi import refresh

    monkeypatch.setattr(refresh, "get_http_client", lambda: MockClient())
    monkeypatch.setattr(
        refresh,
        "fetch_userinfo",
        lambda token: _async_value({"id": "real-user-id", "nickname": "PAT User"}),
    )

    account = await exchange_pat("pt-secret")
    assert account.user_id == "real-user-id"
    assert account.name == "PAT User"
    assert account.access_token == "jt-token"
    assert now_ms + 86_300_000 <= account.expires_at <= now_ms + 86_500_000


@pytest.mark.asyncio
async def test_exchange_pat_accepts_job_token_field(monkeypatch):
    class MockResp:
        status_code = 200
        text = ""

        def json(self):
            return {"jobToken": "jt-token", "expires_in": 86_400_000}

    class MockClient:
        async def post(self, *args, **kwargs):
            return MockResp()

    from qoder2oapi import refresh

    monkeypatch.setattr(refresh, "get_http_client", lambda: MockClient())
    monkeypatch.setattr(
        refresh,
        "fetch_userinfo",
        lambda token: _async_value({"id": "real-user-id"}),
    )

    account = await exchange_pat("pt-secret")
    assert account.access_token == "jt-token"
    assert account.user_id == "real-user-id"


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_pat_refresh_200_updates_token(monkeypatch):
    class MockResp:
        status_code = 200

        def json(self):
            return {
                "code": 0,
                "data": {
                    "token": "jt-new-token",
                    "refresh_token": "rt-new-token",
                    "expires_in": 3600,
                },
            }

    class MockClient:
        async def post(self, url, *args, **kwargs):
            assert "jobToken/refresh" in str(url)
            return MockResp()

    from qoder2oapi import refresh
    monkeypatch.setattr(refresh, "get_http_client", lambda: MockClient())

    acc = AccountRecord(
        id="acc-pat-refresh",
        kind="pat",
        access_token="jt-old",
        refresh_token="rt-old",
        pat="pt-secret",
        user_id="u1",
        machine_id="m1",
        expires_at=int(time.time() * 1000) + 60_000,  # 1 min left (< 5 mins)
    )
    ts_mod.token_store.upsert(acc)

    res = await ensure_fresh(acc)
    assert res.access_token == "jt-new-token"
    assert res.refresh_token == "rt-new-token"
    assert not res.skip_auth

    saved = ts_mod.token_store.get(acc.id)
    assert saved.access_token == "jt-new-token"


@pytest.mark.asyncio
async def test_pat_force_refresh_does_http_when_not_expired(monkeypatch):
    calls = 0

    class MockResp:
        status_code = 200

        def json(self):
            return {
                "data": {
                    "token": "jt-forced",
                    "refresh_token": "jrt-forced",
                    "expires_in": 3600,
                }
            }

    class MockClient:
        async def post(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            return MockResp()

    from qoder2oapi import refresh

    monkeypatch.setattr(refresh, "get_http_client", lambda: MockClient())
    account = AccountRecord(
        id="acc-pat-force",
        kind="pat",
        access_token="jt-old",
        refresh_token="jrt-old",
        pat="pt-secret",
        user_id="real-user-id",
        machine_id="m1",
        expires_at=int(time.time() * 1000) + 60 * 60 * 1000,
    )

    refreshed = await ensure_fresh(account, force=True)
    assert calls == 1
    assert refreshed.access_token == "jt-forced"
    assert refreshed.skip_auth is False


@pytest.mark.asyncio
async def test_pat_refresh_fail_then_exchange_200(monkeypatch):
    class MockRefreshFailResp:
        status_code = 400
        text = "refresh expired"

        def json(self):
            return {"code": 400}

    class MockExchangeSuccessResp:
        status_code = 200

        def json(self):
            return {
                "code": 0,
                "data": {
                    "token": "jt-exchanged-token",
                    "refresh_token": "rt-exchanged-token",
                    "expires_in": 7200,
                },
            }

    calls = []

    class MockClient:
        async def post(self, url, *args, **kwargs):
            calls.append(str(url))
            if "jobToken/refresh" in str(url):
                return MockRefreshFailResp()
            return MockExchangeSuccessResp()

    from qoder2oapi import refresh
    monkeypatch.setattr(refresh, "get_http_client", lambda: MockClient())

    acc = AccountRecord(
        id="acc-pat-failover",
        kind="pat",
        access_token="jt-old",
        refresh_token="rt-old",
        pat="pt-secret",
        user_id="u1",
        machine_id="m1",
        expires_at=0,
    )
    ts_mod.token_store.upsert(acc)

    res = await ensure_fresh(acc)
    assert len(calls) == 2
    assert "jobToken/refresh" in calls[0]
    assert "jobToken/exchange" in calls[1]
    assert res.access_token == "jt-exchanged-token"
    assert not res.skip_auth


@pytest.mark.asyncio
async def test_pat_both_fail_sets_skip_auth(monkeypatch):
    class MockFailResp:
        status_code = 401
        text = "unauthorized"

        def json(self):
            return {"code": 401}

    class MockClient:
        async def post(self, *args, **kwargs):
            return MockFailResp()

    from qoder2oapi import refresh
    monkeypatch.setattr(refresh, "get_http_client", lambda: MockClient())

    acc = AccountRecord(
        id="acc-pat-both-fail",
        kind="pat",
        access_token="jt-old",
        refresh_token="rt-old",
        pat="pt-invalid",
        user_id="u1",
        machine_id="m1",
        expires_at=0,
    )
    ts_mod.token_store.upsert(acc)

    res = await ensure_fresh(acc)
    assert res.skip_auth is True
    assert "failed" in res.last_error.lower()
    saved = ts_mod.token_store.get(acc.id)
    assert saved.skip_auth is True


@pytest.mark.asyncio
async def test_pat_refresh_fail_with_auto_mark_auth_false(monkeypatch):
    from qoder2oapi.runtime_settings import runtime_settings

    runtime_settings.update(auto_mark_auth=False)

    class MockFailResp:
        status_code = 401
        text = "unauthorized"

        def json(self):
            return {"code": 401}

    class MockClient:
        async def post(self, *args, **kwargs):
            return MockFailResp()

    from qoder2oapi import refresh
    monkeypatch.setattr(refresh, "get_http_client", lambda: MockClient())

    acc = AccountRecord(
        id="acc-pat-fail-nomark",
        kind="pat",
        access_token="jt-old",
        refresh_token="rt-old",
        pat="pt-invalid",
        user_id="u1",
        machine_id="m1",
        expires_at=0,
    )
    ts_mod.token_store.upsert(acc)

    res = await ensure_fresh(acc)
    assert res.skip_auth is False
    assert "failed" in res.last_error.lower()
    saved = ts_mod.token_store.get(acc.id)
    assert saved.skip_auth is False
    assert saved.last_error != ""
