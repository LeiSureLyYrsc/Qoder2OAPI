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

    with pytest.raises(HTTPException) as exc_info:
        await execute_infer(_make_request(stream=False), stream=False)
    assert exc_info.value.status_code == 112


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

    # Reset pool active pointer so subsequent tests have clean state
    pool._active_id = None
    pool.clear_cooldown()


@pytest.mark.asyncio
async def test_non_streaming_quota_and_429_failover_to_next_candidate(monkeypatch):
    ts_mod.token_store.clear()
    pool._cursor = 0
    pool._active_id = None
    pool.clear_cooldown()

    ts_mod.token_store.save_all(
        [
            AccountRecord(
                id="acc-q-err",
                kind="oauth",
                access_token="tok-1",
                user_id="user-1",
                machine_id="m-1",
                expires_at=9999999999999,
            ),
            AccountRecord(
                id="acc-success",
                kind="oauth",
                access_token="tok-2",
                user_id="user-2",
                machine_id="m-2",
                expires_at=9999999999999,
            ),
        ]
    )
    catalog_manager.models_by_key = {"auto": {"key": "auto"}}
    _runtime().dispatch_mode = "poll"

    from qoder2oapi import infer

    calls = []

    class MockFailoverClient:
        def build_request(self, method, url=None, content=None, headers=None):
            return {"user": (headers or {}).get("Cosy-User", "")}

        async def send(self, req, stream=False):
            user = req.get("user", "")
            calls.append(user)
            if user == "user-1":
                # Returns 200 with SSE error body code 112
                err_body = json.dumps({"code": 112, "message": "quota exceeded"})
                lines = [f'data: {{"statusCodeValue":200,"body":{json.dumps(err_body)}}}']
                return _MockHttpxResponse(200, stream_lines=lines)
            else:
                return _MockHttpxResponse(200, stream_lines=list(_SUCCESS_SSE))

    monkeypatch.setattr(infer, "get_http_client", lambda: MockFailoverClient())

    result = await execute_infer(_make_request("auto", stream=False), stream=False)
    assert result.status_code == 200
    assert calls == ["user-1", "user-2"]
    payload = json.loads(result.body)
    assert payload["choices"][0]["message"]["content"] == "hi"

    # Verify acc-q-err was marked skip_quota or error
    rec1 = ts_mod.token_store.get("acc-q-err")
    assert rec1 is not None
    assert "Accumulate quota error" in rec1.last_error

    pool.clear_cooldown()


@pytest.mark.asyncio
async def test_streaming_peek_failover(monkeypatch):
    ts_mod.token_store.clear()
    pool._cursor = 0
    pool._active_id = None
    pool.clear_cooldown()

    ts_mod.token_store.save_all(
        [
            AccountRecord(
                id="acc-stream-429",
                kind="oauth",
                access_token="tok-s1",
                user_id="user-s1",
                machine_id="m-s1",
                expires_at=9999999999999,
            ),
            AccountRecord(
                id="acc-stream-ok",
                kind="oauth",
                access_token="tok-s2",
                user_id="user-s2",
                machine_id="m-s2",
                expires_at=9999999999999,
            ),
        ]
    )
    catalog_manager.models_by_key = {"auto": {"key": "auto"}}
    _runtime().dispatch_mode = "poll"

    from qoder2oapi import infer

    calls = []

    class MockStreamClient:
        def build_request(self, method, url=None, content=None, headers=None):
            return {"user": (headers or {}).get("Cosy-User", "")}

        async def send(self, req, stream=False):
            user = req.get("user", "")
            calls.append(user)
            if user == "user-s1":
                # First event is an SSE error frame (e.g. 429)
                err_body = json.dumps({"code": 429, "message": "Too Many Requests"})
                lines = [f'data: {{"statusCodeValue":200,"body":{json.dumps(err_body)}}}']
                return _MockHttpxResponse(200, stream_lines=lines)
            else:
                return _MockHttpxResponse(200, stream_lines=list(_SUCCESS_SSE))

    monkeypatch.setattr(infer, "get_http_client", lambda: MockStreamClient())

    streaming_resp = await execute_infer(_make_request("auto", stream=True), stream=True)
    assert calls == ["user-s1", "user-s2"]
    assert pool.is_rate_limited("acc-stream-429")

    # Read chunks from the streaming response
    chunks = []
    async for chunk in streaming_resp.body_iterator:
        chunks.append(chunk)

    full_text = "".join(chunks)
    assert "data: " in full_text
    assert "[DONE]" in full_text

    pool.clear_cooldown()


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
