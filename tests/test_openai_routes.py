import time
from typing import Any
import httpx
import pytest
from qoder2oapi.app import create_app
from qoder2oapi.catalog import catalog_manager
from qoder2oapi.config import settings
from qoder2oapi.models import AccountRecord
import qoder2oapi.token_store as ts_mod


@pytest.fixture
def test_client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def mock_fetch_quota():
    async def _fetch_quota():
        return {
            "object": "list",
            "total_usage": 10.0,
            "hard_limit": 100.0,
            "user_quota": {"total": 100, "used": 95, "remaining": 5, "unit": "credits"},
            "add_on_quota": {"total": 50, "used": 40, "remaining": 10, "unit": "credits"},
            "dedicated_resource_packages": [
                {"title": "QwQ", "plan_name": "QwQ", "total": 100, "used": 50, "remaining": 50, "unit": "credits", "available": True}
            ],
            "expires_at": 2000,
            "user_type": "personal",
            "is_quota_exceeded": False,
            "accounts": [
                {
                    "id": "acc-1",
                    "user_id": "u-1",
                    "email": "a@b.com",
                    "name": "Tester",
                    "kind": "pat",
                    "client": "cli",
                    "user_type": "personal",
                    "expires_at": 2000,
                    "quota": {
                        "user_quota": {"total": 100, "used": 95, "remaining": 5, "unit": "credits"},
                        "add_on_quota": {"total": 50, "used": 40, "remaining": 10, "unit": "credits"},
                        "dedicated_resource_packages": [
                            {"title": "QwQ", "plan_name": "QwQ", "total": 100, "used": 50, "remaining": 50, "unit": "credits", "available": True}
                        ],
                    },
                    "flags": {"enabled": True, "skip_quota": False, "skip_auth": False},
                }
            ],
        }
    return _fetch_quota


@pytest.mark.asyncio
async def test_missing_api_key_401(test_client):
    async with test_client as client:
        resp = await client.get("/v1/models")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_with_api_key_models_mock(test_client):
    catalog_manager.raw_models = [
        {"key": "dmodel", "display_name": "dmodel"},
        {"key": "qmodel_latest", "display_name": "qmodel_latest"},
        {"key": "auto", "display_name": "Auto"},
    ]
    catalog_manager.models_by_key = {
        item["key"]: item for item in catalog_manager.raw_models
    }
    async with test_client as client:
        headers = {"Authorization": f"Bearer {settings.qoder2oapi_api_key}"}
        resp = await client.get("/v1/models", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        ids = [m["id"] for m in data["data"]]
        assert ids == ["cn/deepseek-v4-pro", "cn/qwen3.7-max", "cn/auto"]
        assert "dmodel" not in ids


@pytest.mark.asyncio
async def test_health_check_no_auth(test_client):
    async with test_client as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_post_completions_no_usable_account_401(test_client):
    async with test_client as client:
        headers = {"Authorization": f"Bearer {settings.qoder2oapi_api_key}"}
        resp = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert resp.status_code == 401
        assert "No usable Qoder account" in resp.text


@pytest.mark.asyncio
async def test_admin_export_import_and_clear_flags(test_client):
    from qoder2oapi.models import AccountRecord
    import qoder2oapi.token_store as ts_mod

    ts_mod.token_store.clear()
    ts_mod.token_store.upsert(
        AccountRecord(
            id="acc-1",
            kind="oauth",
            access_token="secret-token",
            user_id="user-1",
            machine_id="m-1",
            skip_quota=True,
            last_error="quota",
        )
    )
    headers = {"Authorization": f"Bearer {settings.qoder2oapi_api_key}"}
    async with test_client as client:
        listed = await client.get("/api/admin/accounts", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["accounts"][0]["access_token"] == "***"

        exported = await client.get("/api/admin/accounts/export", headers=headers)
        assert exported.status_code == 200
        assert "qoder2oapi-accounts.json" in exported.headers.get("content-disposition", "")
        payload = exported.json()
        assert payload["accounts"][0]["access_token"] == "secret-token"

        patched = await client.patch(
            "/api/admin/accounts/acc-1",
            headers=headers,
            json={"skip_quota": False, "skip_auth": False},
        )
        assert patched.status_code == 200
        assert patched.json()["account"]["skip_quota"] is False
        cleared = ts_mod.token_store.get("acc-1")
        assert cleared is not None
        assert cleared.last_error == ""

        imported = await client.post(
            "/api/admin/accounts/import",
            headers=headers,
            json={
                "mode": "merge",
                "accounts": [
                    {
                        "kind": "oauth",
                        "access_token": "secret-2",
                        "user_id": "user-2",
                        "machine_id": "m-2",
                    }
                ],
            },
        )
        assert imported.status_code == 200
        body = imported.json()
        assert body["imported"] == 1
        assert body["total"] == 2

        bad_mode = await client.post(
            "/api/admin/accounts/import",
            headers=headers,
            json={"mode": "wipe", "accounts": []},
        )
        assert bad_mode.status_code == 400


@pytest.mark.asyncio
async def test_admin_settings_get_and_patch_and_clear_flags(test_client):
    from qoder2oapi.models import AccountRecord
    import qoder2oapi.token_store as ts_mod

    ts_mod.token_store.clear()
    ts_mod.token_store.upsert(
        AccountRecord(
            id="acc-settings-test",
            kind="oauth",
            access_token="tok",
            user_id="u1",
            machine_id="m1",
            skip_quota=True,
            skip_auth=True,
            last_error="some error",
        )
    )

    headers = {"Authorization": f"Bearer {settings.qoder2oapi_api_key}"}
    async with test_client as client:
        # GET initial settings
        resp = await client.get("/api/admin/settings", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["auto_mark_quota"] is False
        assert data["auto_mark_auth"] is True

        # Turn auto_mark_quota to True
        resp = await client.patch(
            "/api/admin/settings",
            headers=headers,
            json={"auto_mark_quota": True},
        )
        assert resp.status_code == 200
        assert resp.json()["auto_mark_quota"] is True
        assert resp.json()["auto_mark_auth"] is True

        # Now turning auto_mark_quota to False clears skip_quota on accounts
        # Note: skip_auth is still True on acc-settings-test, so last_error remains because skip_auth is still active
        resp = await client.patch(
            "/api/admin/settings",
            headers=headers,
            json={"auto_mark_quota": False},
        )
        assert resp.status_code == 200
        assert resp.json()["auto_mark_quota"] is False

        acc = ts_mod.token_store.get("acc-settings-test")
        assert acc is not None
        assert acc.skip_quota is False
        assert acc.skip_auth is True
        assert acc.last_error == "some error"

        # Now turning auto_mark_auth to False clears skip_auth, and since neither skip flag remains, last_error is cleared
        resp = await client.patch(
            "/api/admin/settings",
            headers=headers,
            json={"auto_mark_auth": False},
        )
        assert resp.status_code == 200
        assert resp.json()["auto_mark_auth"] is False

        acc = ts_mod.token_store.get("acc-settings-test")
        assert acc is not None
        assert acc.skip_auth is False
        assert acc.skip_quota is False
        assert acc.last_error == ""



@pytest.mark.asyncio
async def test_admin_dispatch_settings_and_model_override(test_client):
    catalog_manager.raw_models = [
        {"key": "auto", "display_name": "Auto"},
        {"key": "dfmodel", "display_name": "DeepSeek Flash"},
    ]
    catalog_manager.models_by_key = {m["key"]: m for m in catalog_manager.raw_models}

    headers = {"Authorization": f"Bearer {settings.qoder2oapi_api_key}"}
    async with test_client as client:
        initial = await client.get("/api/admin/settings", headers=headers)
        assert initial.status_code == 200
        assert initial.json()["dispatch_mode"] == "poll"
        assert initial.json()["model_dispatch"] == {}

        patched = await client.patch(
            "/api/admin/settings", headers=headers, json={"dispatch_mode": "failover"}
        )
        assert patched.status_code == 200
        assert patched.json()["dispatch_mode"] == "failover"

        model_patch = await client.patch(
            "/api/admin/models/dfmodel/dispatch",
            headers=headers,
            json={"mode": "poll_include_quota"},
        )
        assert model_patch.status_code == 200
        assert model_patch.json()["mode"] == "poll_include_quota"

        listed = await client.get("/api/admin/models", headers=headers)
        assert listed.status_code == 200
        modes = {m["key"]: m["dispatch_mode"] for m in listed.json()["models"]}
        assert modes["dfmodel"] == "poll_include_quota"
        assert modes["auto"] == "inherit"

        reset = await client.patch(
            "/api/admin/models/dfmodel/dispatch",
            headers=headers,
            json={"mode": "inherit"},
        )
        assert reset.status_code == 200
        assert reset.json()["mode"] == "inherit"

        invalid = await client.patch(
            "/api/admin/models/dfmodel/dispatch",
            headers=headers,
            json={"mode": "nonsense"},
        )
        assert invalid.status_code == 400


@pytest.mark.asyncio
async def test_chat_completions_per_model_poll_include_quota(test_client, monkeypatch):
    from qoder2oapi import infer

    ts_mod.token_store.clear()
    ts_mod.token_store.upsert(
        AccountRecord(
            id="quota-only",
            kind="oauth",
            access_token="tok-q",
            user_id="user-q",
            machine_id="m-q",
            expires_at=9999999999999,
            skip_quota=True,
        )
    )
    catalog_manager.models_by_key = {"auto": {"key": "auto"}}

    class MockResp:
        status_code = 200

        async def aiter_lines(self):
            yield (
                'data: {"statusCodeValue":200,"body":"{\\"id\\":\\"c1\\",\\"choices\\":'
                '[{\\"delta\\":{\\"role\\":\\"assistant\\",\\"content\\":\\"hi\\"}}]}"}'
            )
            yield "data: [DONE]"

        async def aread(self):
            return b""

        async def aclose(self):
            pass

    class MockClient:
        def build_request(self, *args, **kwargs):
            return None

        async def send(self, req, stream=False):
            return MockResp()

    monkeypatch.setattr(infer, "get_http_client", lambda: MockClient())

    headers = {"Authorization": f"Bearer {settings.qoder2oapi_api_key}"}
    body = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
    async with test_client as client:
        blocked = await client.post("/v1/chat/completions", headers=headers, json=body)
        assert blocked.status_code == 429

        patched = await client.patch(
            "/api/admin/models/auto/dispatch",
            headers=headers,
            json={"mode": "poll_include_quota"},
        )
        assert patched.status_code == 200

        allowed = await client.post("/v1/chat/completions", headers=headers, json=body)
        assert allowed.status_code == 200
        assert allowed.json()["choices"][0]["message"]["content"] == "hi"


@pytest.mark.asyncio
async def test_add_pat_refreshes_model_catalog(test_client, monkeypatch):
    from qoder2oapi.models import AccountRecord
    from qoder2oapi.routes import admin

    refreshed = False

    async def mock_exchange_pat(pat):
        assert pat == "pt-secret"
        return AccountRecord(
            id="pat-account",
            kind="pat",
            access_token="jt-token",
            refresh_token="jrt-token",
            pat=pat,
            user_id="real-user-id",
            machine_id="machine-id",
            expires_at=9_999_999_999_999,
        )

    async def mock_fetch_models():
        nonlocal refreshed
        refreshed = True
        return [{"key": "auto"}]

    monkeypatch.setattr(admin, "exchange_pat", mock_exchange_pat)
    monkeypatch.setattr(admin.catalog_manager, "fetch_models", mock_fetch_models)

    headers = {"Authorization": f"Bearer {settings.qoder2oapi_api_key}"}
    async with test_client as client:
        response = await client.post(
            "/api/admin/accounts/pat",
            headers=headers,
            json={"pat": "pt-secret"},
        )
    assert response.status_code == 200
    assert response.json()["account"]["user_id"] == "real-user-id"
    assert refreshed is True


@pytest.mark.asyncio
async def test_old_admin_quota_route_returns_404(test_client):
    async with test_client as client:
        headers = {"Authorization": f"Bearer {settings.qoder2oapi_api_key}"}
        resp = await client.get("/api/admin/quota", headers=headers)
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_billing_credits_route(mock_fetch_quota, monkeypatch, test_client):
    from qoder2oapi import quota
    monkeypatch.setattr(quota, "fetch_quota", mock_fetch_quota)

    async with test_client as client:
        headers = {"Authorization": f"Bearer {settings.qoder2oapi_api_key}"}
        resp = await client.get("/v1/dashboard/billing/credits", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "billing_credits"
        assert "general" in data
        assert "addon" in data
        assert "dedicated" in data
        assert "accounts" in data
        assert data["accounts"][0]["remaining"] == 65


@pytest.mark.asyncio
async def test_model_catalog_uses_cli_identity_when_fetching(monkeypatch, test_client):
    from qoder2oapi import catalog
    catalog_manager.raw_models = []
    catalog_manager.models_by_key = {}
    fetched_creds: dict[str, Any] | None = None

    class MockResp:
        status_code = 200

        def json(self):
            return {
                "body": {
                    "chat": [
                        {"key": "my-model-key", "display_name": "My Model", "enable": False},
                    ]
                }
            }

    class MockClient:
        async def get(self, url, headers=None, **kwargs):
            nonlocal fetched_creds
            fetched_creds = {"client": headers.get("Cosy-Clienttype"), "session": headers.get("Cosy-Business-Product")}
            return MockResp()

    monkeypatch.setattr(catalog, "get_http_client", lambda: MockClient())

    ts_mod.token_store.clear()
    ts_mod.token_store.upsert(
        AccountRecord(
            id="acc-cli-model",
            kind="oauth",
            client="desktop",
            access_token="tok-model",
            user_id="u-model",
            machine_id="m-model",
            expires_at=int(time.time() * 1000) + 60 * 60 * 1000,
        )
    )

    async with test_client as client:
        headers = {"Authorization": f"Bearer {settings.qoder2oapi_api_key}"}
        resp = await client.get("/v1/models", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        ids = [m["id"] for m in data["data"]]
        assert "cn/my-model-key" in ids
        assert fetched_creds is not None
        assert fetched_creds["client"] == "5"
        assert fetched_creds["session"] is None

