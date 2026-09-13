import httpx
import pytest
from qoder2oapi.app import create_app
from qoder2oapi.catalog import catalog_manager
from qoder2oapi.config import settings


@pytest.fixture
def test_client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


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
