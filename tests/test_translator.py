import time
import pytest
from qoder2oapi.catalog import CatalogManager, _extract_chat_list, catalog_manager
from qoder2oapi.models import AccountRecord
from qoder2oapi.models import ChatCompletionRequest, ChatMessage
from qoder2oapi.translator import translate_openai_to_qoder


@pytest.fixture(autouse=True)
def setup_catalog():
    catalog_manager.models_by_key = {
        "claude-3-7-sonnet": {
            "key": "claude-3-7-sonnet",
            "display_name": "Claude 3.7 Sonnet",
            "is_reasoning": True,
            "max_output_tokens": 64000,
            "context_config": [
                {"name": "200k", "tokenCount": 200000, "isDefault": True},
                {"name": "1m", "tokenCount": 1000000, "isDefault": False},
            ],
            "model_config": {
                "source": "anthropic",
                "thinking_levels": ["low", "medium", "high", "max"],
            },
        },
        "gpt-4o": {
            "key": "gpt-4o",
            "display_name": "GPT-4o",
            "is_reasoning": False,
            "max_output_tokens": 16384,
            "context_config": [
                {"name": "128k", "tokenCount": 128000, "isDefault": True},
            ],
            "model_config": {
                "source": "openai",
            },
        },
        "dmodel": {
            "key": "dmodel",
            "display_name": "dmodel",
            "is_reasoning": True,
            "max_input_tokens": 180000,
            "max_output_tokens": 32768,
            "context_config": {
                "tiers": [
                    {"name": "180k", "token_count": 180000, "isDefault": True},
                    {"label": "1M", "max_input_tokens": "1M"},
                ]
            },
        },
    }


def test_system_hoisting_and_max_context_and_thinking():
    req = ChatCompletionRequest(
        model="claude-3-7-sonnet",
        messages=[
            ChatMessage(role="system", content="You are a helpful assistant."),
            ChatMessage(role="user", content="Hello!"),
        ],
    )
    payload, model_data = translate_openai_to_qoder(req, user_id="user_123")

    assert payload["system"] == "You are a helpful assistant."
    assert len(payload["messages"]) == 1
    assert payload["messages"][0]["role"] == "user"
    assert payload["messages"][0]["content"] == "Hello!"

    assert payload["parameters"]["context_length"] == 1000000
    assert payload["chat_context"]["extra"]["ideModelConfigOverride"]["max_input_tokens"] == 1000000
    assert payload["parameters"]["reasoning_effort"] == "max"
    assert payload["chat_context"]["extra"]["ideModelConfigOverride"]["reasoning_effort"] == "max"
    assert payload["model_config"]["key"] == "claude-3-7-sonnet"
    assert payload["model_config"]["max_input_tokens"] == 1000000
    assert payload["model_config"]["reasoning_effort"] == "max"
    assert payload["is_reply"] is True
    assert payload["code_language"] == ""
    assert payload["aliyun_user_type"] == ""


def test_client_reasoning_effort_override():
    req = ChatCompletionRequest(
        model="claude-3-7-sonnet",
        messages=[
            ChatMessage(role="user", content="Explain quantum physics"),
        ],
        reasoning_effort="low",
    )
    payload, _ = translate_openai_to_qoder(req, user_id="user_123")
    assert payload["parameters"]["reasoning_effort"] == "low"
    assert payload["chat_context"]["extra"]["ideModelConfigOverride"]["reasoning_effort"] == "low"


def test_tools_and_tool_role_passed():
    req = ChatCompletionRequest(
        model="gpt-4o",
        messages=[
            ChatMessage(role="user", content="What's the weather?"),
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city":"Tokyo"}'},
                    }
                ],
            ),
            ChatMessage(role="tool", content="22C sunny", tool_call_id="call_1"),
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ],
    )
    payload, _ = translate_openai_to_qoder(req, user_id="user_123")

    assert len(payload["tools"]) == 1
    assert payload["tools"][0]["function"]["name"] == "get_weather"
    assert len(payload["messages"]) == 3
    assert payload["messages"][1]["role"] == "assistant"
    assert payload["messages"][1]["tool_calls"][0]["id"] == "call_1"
    assert payload["messages"][2]["role"] == "tool"
    assert payload["messages"][2]["tool_call_id"] == "call_1"
    assert payload["messages"][2]["content"] == "22C sunny"
    assert payload["is_reply"] is False
    assert payload["chat_prompt"] == ""
    assert payload["image_urls"] is None
    assert payload["chat_context"]["extra"]["originalContent"] == "What's the weather?"
    assert payload["chat_context"]["extra"]["modelConfig"]["key"] == "gpt-4o"


def test_keyed_context_config_uses_largest_window():
    model = {
        "key": "auto",
        "max_input_tokens": 180000,
        "context_config": {
            "128k": {"token_count": 128000, "is_default": False},
            "180k": {"token_count": 180000, "is_default": True},
        },
        "available_context_windows": [128000, 180000],
        "thinking_config": {
            "supported_efforts": ["low", "medium", "high"],
            "default_effort": "medium",
        },
        "is_reasoning": True,
    }
    assert catalog_manager.get_max_context_length(model) == 180000
    assert catalog_manager.get_min_context_length(model) == 128000
    assert catalog_manager.determine_thinking_levels(model) == ["low", "medium", "high"]
    assert catalog_manager.get_default_thinking(model) == "medium"


def test_scene_map_catalog_and_missing_max_input():
    data = {
        "default": [
            {
                "key": "lite",
                "display_name": "lite",
                "context_config": {
                    "200k": {"token_count": 200000, "is_default": True},
                },
            }
        ],
        "byok_enterprise": [],
    }
    chat = _extract_chat_list(data)
    assert chat[0]["key"] == "lite"
    assert catalog_manager.get_max_context_length(chat[0]) == 200000


def test_scene_map_merges_all_scenes_with_preferred_precedence():
    data = {
        "chat": [
            {"key": "auto", "display_name": "Auto"},
            {"key": "dfmodel", "display_name": "DeepSeek-Flash"},
        ],
        "default": [
            {"key": "lite", "display_name": "Lite"},
            {"key": "dfmodel", "display_name": "Old DeepSeek name"},
        ],
        "enterprise": [
            {"key": "gfmodel", "display_name": "GLM-5.3-Flash"},
        ],
        "metadata": {"version": 1},
    }
    models = _extract_chat_list(data)
    assert [item["key"] for item in models] == ["auto", "dfmodel", "lite", "gfmodel"]
    assert models[1]["display_name"] == "DeepSeek-Flash"


@pytest.mark.asyncio
async def test_pat_catalog_prefers_algo_encode_and_retries_after_auth(monkeypatch):
    account = AccountRecord(
        id="pat-account",
        kind="pat",
        access_token="jt-old",
        refresh_token="jrt-old",
        pat="pt-secret",
        user_id="real-user-id",
        machine_id="machine-id",
        expires_at=int(time.time() * 1000) + 60 * 60 * 1000,
    )
    calls = []

    class MockResponse:
        def __init__(self, status_code, data=None):
            self.status_code = status_code
            self._data = data or {}

        def json(self):
            return self._data

    class MockClient:
        async def get(self, url, headers=None):
            calls.append((url, headers["Cosy-User"]))
            if len(calls) == 1:
                return MockResponse(401)
            return MockResponse(200, {"chat": [{"key": "auto"}]})

    async def mock_ensure_fresh(current, force=False):
        if force:
            current.access_token = "jt-new"
        return current

    manager = CatalogManager()
    from qoder2oapi import catalog

    monkeypatch.setattr(catalog.token_store, "list_accounts", lambda: [account])
    monkeypatch.setattr(catalog, "get_http_client", lambda: MockClient())
    monkeypatch.setattr(catalog, "ensure_fresh", mock_ensure_fresh)

    models = await manager.fetch_models()
    assert models == [{"key": "auto"}]
    assert len(calls) == 2
    assert calls[0][0].endswith("/algo/api/v2/model/list?Encode=1")
    assert calls[0][1] == "real-user-id"


def test_extract_chat_list_from_string_envelope():
    data = {
        "statusCodeValue": 200,
        "body": '{"chat":[{"key":"auto","display_name":"Auto","is_reasoning":true}]}',
    }
    chat = _extract_chat_list(data)
    assert chat[0]["key"] == "auto"


def test_nested_context_config_defaults_to_max_tier():
    req = ChatCompletionRequest(
        model="deepseek-v4-pro",
        messages=[ChatMessage(role="user", content="hi")],
    )
    payload, _ = translate_openai_to_qoder(req, user_id="user_123")
    assert payload["parameters"]["context_length"] == 1000000
    assert payload["model_config"]["max_input_tokens"] == 1000000
    assert payload["chat_context"]["extra"]["modelConfig"]["key"] == "dmodel"


def test_client_context_length_override_clamped():
    req = ChatCompletionRequest(
        model="dmodel",
        messages=[ChatMessage(role="user", content="hi")],
        extra_body={"context_length": 200000},
    )
    payload, _ = translate_openai_to_qoder(req, user_id="user_123")
    assert payload["parameters"]["context_length"] == 200000

    too_small = ChatCompletionRequest(
        model="dmodel",
        messages=[ChatMessage(role="user", content="hi")],
        extra_body={"context_length": 1000},
    )
    payload_small, _ = translate_openai_to_qoder(too_small, user_id="user_123")
    assert payload_small["parameters"]["context_length"] == 180000
