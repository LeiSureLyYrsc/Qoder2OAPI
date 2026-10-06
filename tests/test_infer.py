import json
import httpx
import pytest
from fastapi import HTTPException

from qoder2oapi.app import create_app
from qoder2oapi.catalog import catalog_manager
from qoder2oapi.config import settings
from qoder2oapi.infer import execute_infer
from qoder2oapi.models import AccountRecord, ChatCompletionRequest, ChatMessage
from qoder2oapi.pool import pool
import qoder2oapi.token_store as ts_mod


def _make_request(model: str = "auto", stream: bool = False) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content="hello")],
        stream=stream,
    )


class _MockHttpxResponse:
    def __init__(self, status_code: int, body: bytes = b"", text: str = "", stream_lines: list[str] | None = None):
        self.status_code = status_code
        self._body = body or text.encode("utf-8")
        self._stream_lines = stream_lines or []
        self.closed = False

    async def aread(self):
        return self._body

    async def aclose(self):
        self.closed = True

    async def aiter_lines(self):
        for line in self._stream_lines:
            yield line


@pytest.fixture
def test_client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_upstream_429_returned_directly(monkeypatch):
    ts_mod.token_store.clear()
    ts_mod.token_store.upsert(
        AccountRecord(
            id="acc-429",
            kind="oauth",
            access_token="tok-429",
            user_id="u-429",
            machine_id="m-429",
            expires_at=9999999999999,
        )
    )
    catalog_manager.models_by_key = {"auto": {"key": "auto"}}

    class MockClient:
        async def send(self, req, stream=False):
            return _MockHttpxResponse(429, text="rate limited")

        def build_request(self, *args, **kwargs):
            return None

    from qoder2oapi import infer
    monkeypatch.setattr(infer, "get_http_client", lambda: MockClient())

    # 429 is retried/marked as quota; with only one account the final answer is HTTP 429.
    with pytest.raises(HTTPException) as exc_info:
        await execute_infer(_make_request(stream=False), stream=False)
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_upstream_402_returned_directly(monkeypatch):
    ts_mod.token_store.clear()
    ts_mod.token_store.upsert(
        AccountRecord(
            id="acc-402",
            kind="oauth",
            access_token="tok-402",
            user_id="u-402",
            machine_id="m-402",
            expires_at=9999999999999,
        )
    )
    catalog_manager.models_by_key = {"auto": {"key": "auto"}}

    class MockClient:
        async def send(self, req, stream=False):
            return _MockHttpxResponse(402, text="payment required")

        def build_request(self, *args, **kwargs):
            return None

    from qoder2oapi import infer
    monkeypatch.setattr(infer, "get_http_client", lambda: MockClient())

    result = await execute_infer(_make_request(stream=False), stream=False)
    assert result.status_code == 402
    payload = json.loads(result.body)
    assert payload["error"]["code"] == 402


@pytest.mark.asyncio
async def test_sse_error_code_passed_through(monkeypatch):
    ts_mod.token_store.clear()
    ts_mod.token_store.upsert(
        AccountRecord(
            id="acc-sse-err",
            kind="oauth",
            access_token="tok-sse",
            user_id="u-sse",
            machine_id="m-sse",
            expires_at=9999999999999,
        )
    )
    catalog_manager.models_by_key = {"auto": {"key": "auto"}}

    error_body = json.dumps({"code": 112, "message": "quota exceeded"})
    sse_lines = [
        f'data: {{"statusCodeValue":200,"body":{json.dumps(error_body)}}}',
    ]

    class MockClient:
        async def send(self, req, stream=False):
            return _MockHttpxResponse(200, stream_lines=sse_lines)

        def build_request(self, *args, **kwargs):
            return None

    from qoder2oapi import infer
    monkeypatch.setattr(infer, "get_http_client", lambda: MockClient())

    result = await execute_infer(_make_request(stream=False), stream=False)
    assert result.status_code == 112
    payload = json.loads(result.body)
    assert payload["error"]["code"] == 112


_SUCCESS_SSE = [
    'data: {"statusCodeValue":200,"body":"{\\"id\\":\\"c1\\",\\"choices\\":[{\\"delta\\":{\\"role\\":\\"assistant\\",\\"content\\":\\"hi\\"}}]}"}',
    'data: {"statusCodeValue":200,"body":"{\\"id\\":\\"c1\\",\\"choices\\":[{\\"delta\\":{},\\"finish_reason\\":\\"stop\\"}]}"}',
    "data: [DONE]",
]


def _runtime():
    from qoder2oapi import runtime_settings as rs_module

    return rs_module.runtime_settings


class _DispatchClient:
    def __init__(self, behavior):
        self.behavior = behavior
        self.seen: list[str] = []

    def build_request(self, method, url=None, content=None, headers=None):
        return {"user": (headers or {}).get("Cosy-User", "")}

    async def send(self, req, stream=False):
        user = req.get("user", "")
        self.seen.append(user)
        status = self.behavior(user)
        if status == 200:
            return _MockHttpxResponse(200, stream_lines=list(_SUCCESS_SSE))
        return _MockHttpxResponse(status, text="upstream error")


@pytest.mark.asyncio
async def test_poll_include_quota_serves_quota_marked_account(monkeypatch):
    ts_mod.token_store.clear()
    pool._cursor = 0
    pool._active_id = None
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
    _runtime().set_model_dispatch("auto", "poll_include_quota")

    from qoder2oapi import infer

    mock = _DispatchClient(lambda user: 200)
    monkeypatch.setattr(infer, "get_http_client", lambda: mock)

    result = await execute_infer(_make_request("auto"), stream=False)
    assert result.status_code == 200
    assert mock.seen == ["user-q"]


@pytest.mark.asyncio
async def test_failover_sticks_to_active_and_advances_on_failure(monkeypatch):
    ts_mod.token_store.clear()
    pool._cursor = 0
    pool._active_id = None
    ts_mod.token_store.save_all(
        [
            AccountRecord(
                id="acc-a",
                kind="oauth",
                access_token="tok-a",
                user_id="user-a",
                machine_id="m-a",
                expires_at=9999999999999,
            ),
            AccountRecord(
                id="acc-b",
                kind="oauth",
                access_token="tok-b",
                user_id="user-b",
                machine_id="m-b",
                expires_at=9999999999999,
            ),
        ]
    )
    catalog_manager.models_by_key = {"auto": {"key": "auto"}}
    _runtime().dispatch_mode = "failover"

    from qoder2oapi import infer

    state = {"a_fails": False}

    def decide(user: str) -> int:
        return 429 if (state["a_fails"] and user == "user-a") else 200

    mock = _DispatchClient(decide)
    monkeypatch.setattr(infer, "get_http_client", lambda: mock)

    first = await execute_infer(_make_request("auto"), stream=False)
    assert first.status_code == 200
    assert mock.seen == ["user-a"]
    assert pool._active_id == "acc-a"

    state["a_fails"] = True
    mock.seen = []
    second = await execute_infer(_make_request("auto"), stream=False)
    assert second.status_code == 200
    assert mock.seen == ["user-a", "user-b"]
    assert pool._active_id == "acc-b"

    mock.seen = []
    third = await execute_infer(_make_request("auto"), stream=False)
    assert third.status_code == 200
    assert mock.seen == ["user-b"]


@pytest.mark.asyncio
async def test_admin_models_no_enable_field(test_client):
    catalog_manager.raw_models = [
        {"key": "dmodel", "display_name": "dmodel", "enable": False},
        {"key": "qmodel_latest", "display_name": "qmodel_latest", "enable": True},
    ]
    catalog_manager.models_by_key = {
        item["key"]: item for item in catalog_manager.raw_models
    }

    headers = {"Authorization": f"Bearer {settings.qoder2oapi_api_key}"}
    async with test_client as client:
        resp = await client.get("/api/admin/models", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        models = data["models"]
        assert len(models) == 2
        assert "enable" not in models[0]
        assert "enable" not in models[1]
