import urllib.parse

import pytest
import httpx
from qoder2oapi.oauth import poll_device_flow, start_device_flow
from qoder2oapi.token_store import token_store


def test_cli_device_flow_url_has_no_client_id():
    flow = start_device_flow("cli")
    assert flow["client"] == "cli"
    assert flow["verification_uri"].startswith("https://qoder.com.cn/device/selectAccounts")
    assert "client_id=" not in flow["verification_uri"]
    assert "biz_variant=" not in flow["verification_uri"]


def test_desktop_input_treated_as_cli():
    flow = start_device_flow("desktop")
    assert flow["client"] == "cli"
    assert flow["verification_uri"].startswith("https://qoder.com.cn/device/selectAccounts")
    assert "client_id=" not in flow["verification_uri"]
    assert "biz_variant=" not in flow["verification_uri"]


def test_app_input_treated_as_cli():
    flow = start_device_flow("app")
    assert flow["client"] == "cli"


@pytest.mark.asyncio
async def test_oauth_pending_202(monkeypatch):
    flow = start_device_flow()
    login_id = flow["login_id"]

    class MockPendingResp:
        status_code = 202
        text = "pending"

        def json(self):
            return {}

    async def mock_get(*args, **kwargs):
        return MockPendingResp()

    from qoder2oapi import oauth
    mock_client = type("MockClient", (), {"get": mock_get})()
    monkeypatch.setattr(oauth, "get_http_client", lambda: mock_client)

    res = await poll_device_flow(login_id)
    assert res["status"] == "pending"


@pytest.mark.asyncio
async def test_oauth_success_200_parse(monkeypatch):
    flow = start_device_flow()
    login_id = flow["login_id"]

    class MockSuccessResp:
        status_code = 200

        def json(self):
            return {
                "code": 0,
                "data": {
                    "token": "test-access-token",
                    "refresh_token": "test-refresh-token",
                    "user_id": "u_999",
                    "expires_in": 3600,
                },
            }

    class MockUserInfoResp:
        status_code = 200

        def json(self):
            return {
                "data": {
                    "nickname": "TestUser",
                    "email": "test@domain.com",
                }
            }

    class MockClient:
        async def get(self, url, *args, **kwargs):
            if "poll" in str(url):
                return MockSuccessResp()
            return MockUserInfoResp()

    from qoder2oapi import oauth
    mock_client = MockClient()
    monkeypatch.setattr(oauth, "get_http_client", lambda: mock_client)

    res = await poll_device_flow(login_id)
    assert res.get("status") == "ok", f"poll failed with: {res}"
    assert res["user"]["access_token"] == "***"
    assert res["user"]["user_id"] == "u_999"
    assert res["user"]["name"] == "TestUser"
    assert res["user"]["email"] == "test@domain.com"

    saved = oauth.token_store.load_token()
    assert saved is not None
    assert saved.access_token == "test-access-token"
    assert saved.user_id == "u_999"
    accounts = oauth.token_store.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].kind == "oauth"
    assert accounts[0].client == "cli"


@pytest.mark.asyncio
async def test_oauth_userinfo_id_is_used_when_poll_omits_user_id(monkeypatch):
    flow = start_device_flow()

    class MockPollResp:
        status_code = 200

        def json(self):
            return {"data": {"token": "dt-token", "expires_in": 3600}}

    class MockUserInfoResp:
        status_code = 200

        def json(self):
            return {"data": {"id": "userinfo-id", "nickname": "User"}}

    class MockClient:
        async def get(self, url, *args, **kwargs):
            if "poll" in str(url):
                return MockPollResp()
            return MockUserInfoResp()

    from qoder2oapi import oauth

    monkeypatch.setattr(oauth, "get_http_client", lambda: MockClient())
    result = await poll_device_flow(flow["login_id"])
    assert result["status"] == "ok"
    assert result["user"]["user_id"] == "userinfo-id"


@pytest.mark.asyncio
async def test_desktop_oauth_poll_saves_cli_client(monkeypatch):
    flow = start_device_flow("desktop")

    class MockPollResp:
        status_code = 200

        def json(self):
            return {
                "data": {
                    "token": "dt-desktop",
                    "refresh_token": "rt-desktop",
                    "user_id": "u_desk",
                    "expires_in": 3600,
                }
            }

    class MockUserInfoResp:
        status_code = 200

        def json(self):
            return {"data": {"nickname": "Desk", "email": "desk@example.com"}}

    class MockClient:
        async def get(self, url, *args, **kwargs):
            if "poll" in str(url):
                return MockPollResp()
            return MockUserInfoResp()

    from qoder2oapi import oauth

    monkeypatch.setattr(oauth, "get_http_client", lambda: MockClient())
    result = await poll_device_flow(flow["login_id"])
    assert result["status"] == "ok"
    assert result["user"]["client"] == "cli"
    saved = oauth.token_store.list_accounts()[0]
    assert saved.client == "cli"
    assert saved.kind == "oauth"
