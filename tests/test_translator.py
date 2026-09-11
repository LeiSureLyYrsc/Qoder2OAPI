import pytest
from qoder2oapi.catalog import _extract_chat_list, catalog_manager
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


def test_extract_chat_list_from_string_envelope():
    data = {
        "statusCodeValue": 200,
        "body": '{"chat":[{"key":"auto","display_name":"Auto","is_reasoning":true}]}',
    }
    chat = _extract_chat_list(data)
    assert chat[0]["key"] == "auto"
