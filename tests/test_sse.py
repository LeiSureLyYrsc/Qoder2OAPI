import json
import pytest
from qoder2oapi.sse import accumulate_sse_response, unwrap_sse_stream


async def mock_line_stream(lines: list[str]):
    for line in lines:
        yield line


@pytest.mark.asyncio
async def test_unwrap_sse_stream_tool_calls_and_coalesce():
    raw_lines = [
        'data: {"statusCodeValue":200,"body":"{\\"id\\":\\"c1\\",\\"choices\\":[{\\"delta\\":{\\"role\\":\\"assistant\\",\\"content\\":null,\\"tool_calls\\":[{\\"index\\":0,\\"id\\":\\"call_abc\\",\\"type\\":\\"function\\",\\"function\\":{\\"name\\":\\"fetch_user\\",\\"arguments\\":\\"{\\\\\\"id\\\\\\":\\\\\\"123\\\\\\"}\\"}}]}}]}"}',
        'data: {"statusCodeValue":200,"body":"{\\"id\\":\\"c1\\",\\"choices\\":[{\\"delta\\":{},\\"finish_reason\\":\\"tool_calls\\"}]}"}',
        'data: {"statusCodeValue":200,"body":"{\\"id\\":\\"c1\\",\\"choices\\":[],\\"usage\\":{\\"prompt_tokens\\":15,\\"completion_tokens\\":20,\\"total_tokens\\":35}}" }',
        'data: [DONE]',
    ]

    unwrapped = []
    async for chunk in unwrap_sse_stream(mock_line_stream(raw_lines)):
        unwrapped.append(chunk)

    assert len(unwrapped) == 3
    c1 = json.loads(unwrapped[0].removeprefix("data: ").strip())
    assert c1["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "fetch_user"

    c2 = json.loads(unwrapped[1].removeprefix("data: ").strip())
    assert c2["choices"][0]["finish_reason"] == "tool_calls"
    assert c2["usage"]["total_tokens"] == 35

    assert unwrapped[2].strip() == "data: [DONE]"


@pytest.mark.asyncio
async def test_accumulate_sse_response():
    raw_lines = [
        'data: {"statusCodeValue":200,"body":"{\\"id\\":\\"c1\\",\\"choices\\":[{\\"delta\\":{\\"role\\":\\"assistant\\",\\"content\\":\\"Hello \\"}}]}"}',
        'data: {"statusCodeValue":200,"body":"{\\"id\\":\\"c1\\",\\"choices\\":[{\\"delta\\":{\\"content\\":\\"world!\\"}}]}"}',
        'data: {"statusCodeValue":200,"body":"{\\"id\\":\\"c1\\",\\"choices\\":[{\\"delta\\":{},\\"finish_reason\\":\\"stop\\"}]}"}',
        'data: {"statusCodeValue":200,"body":"{\\"id\\":\\"c1\\",\\"choices\\":[],\\"usage\\":{\\"prompt_tokens\\":5,\\"completion_tokens\\":2,\\"total_tokens\\":7}}" }',
        'data: [DONE]',
    ]
    accumulated = await accumulate_sse_response(mock_line_stream(raw_lines))
    assert accumulated["object"] == "chat.completion"
    assert accumulated["choices"][0]["message"]["content"] == "Hello world!"
    assert accumulated["choices"][0]["finish_reason"] == "stop"
    assert accumulated["usage"]["total_tokens"] == 7


@pytest.mark.asyncio
async def test_unwrap_sse_error():
    raw_lines = [
        'data: {"statusCodeValue":500,"body":"Internal Server Error"}',
    ]
    unwrapped = []
    async for chunk in unwrap_sse_stream(mock_line_stream(raw_lines)):
        unwrapped.append(chunk)

    assert len(unwrapped) == 2
    err_json = json.loads(unwrapped[0].removeprefix("data: ").strip())
    assert "error" in err_json
    assert "Internal Server Error" in err_json["error"]["message"]
    assert unwrapped[1].strip() == "data: [DONE]"


@pytest.mark.asyncio
async def test_accumulate_tool_calls_finish_reason():
    raw_lines = [
        'data: {"statusCodeValue":200,"body":"{\\"id\\":\\"c1\\",\\"choices\\":[{\\"delta\\":{\\"tool_calls\\":[{\\"index\\":0,\\"id\\":\\"call_abc\\",\\"type\\":\\"function\\",\\"function\\":{\\"name\\":\\"fetch_user\\",\\"arguments\\":\\"{}\\"}}]}}]}"}',
        'data: {"statusCodeValue":200,"body":"{\\"id\\":\\"c1\\",\\"choices\\":[{\\"delta\\":{},\\"finish_reason\\":\\"stop\\"}]}"}',
        "data: [DONE]",
    ]
    accumulated = await accumulate_sse_response(mock_line_stream(raw_lines))
    assert accumulated["choices"][0]["finish_reason"] == "tool_calls"
    assert accumulated["choices"][0]["message"]["tool_calls"][0]["id"] == "call_abc"


@pytest.mark.asyncio
async def test_accumulate_upstream_error():
    raw_lines = [
        'data: {"statusCodeValue":500,"body":"Internal Server Error"}',
    ]
    accumulated = await accumulate_sse_response(mock_line_stream(raw_lines))
    assert "error" in accumulated
    assert "Internal Server Error" in accumulated["error"]["message"]
