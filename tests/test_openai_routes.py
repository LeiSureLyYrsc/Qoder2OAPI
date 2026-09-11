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
        assert ids == ["deepseek-v4-pro", "qwen3.7-max", "auto"]
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

