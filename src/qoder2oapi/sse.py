from collections.abc import AsyncIterator
import json
import time
from typing import Any


def _upstream_code_from_text(err_msg: str) -> int | None:
    try:
        parsed = json.loads(err_msg)
        if isinstance(parsed, dict):
            code = parsed.get("code")
            if isinstance(code, int):
                return code
            if isinstance(code, str) and code.isdigit():
                return int(code)
    except Exception:
        pass
    return None


def _error_frames(err_msg: str) -> list[str]:
    code = _upstream_code_from_text(err_msg) or 500
    err_obj = {
        "error": {
            "message": err_msg,
            "type": "qoder_error",
            "param": None,
            "code": code,
        }
    }
    return [f"data: {json.dumps(err_obj)}\n\n", "data: [DONE]\n\n"]


async def unwrap_sse_stream(byte_lines: AsyncIterator[str]) -> AsyncIterator[str]:
    pending_finish_choice: dict[str, Any] | None = None
    last_chunk_template: dict[str, Any] | None = None

    async for line in byte_lines:
        line = line.strip()
        if not line:
            continue
        if not line.startswith("data:"):
            continue

        raw_data = line[len("data:") :].strip()
        if not raw_data:
            continue
        if raw_data == "[DONE]":
            if pending_finish_choice is not None and last_chunk_template is not None:
                flush_chunk = dict(last_chunk_template)
                flush_chunk["choices"] = [pending_finish_choice]
                yield f"data: {json.dumps(flush_chunk)}\n\n"
                pending_finish_choice = None
            yield "data: [DONE]\n\n"
            break

        try:
            envelope = json.loads(raw_data)
        except Exception:
            yield f"data: {raw_data}\n\n"
            continue

        status_code = envelope.get("statusCodeValue", 200)
        if status_code != 200:
            err_body = envelope.get("body") or envelope.get("message") or "Upstream error"
            for frame in _error_frames(str(err_body)):
                yield frame
            break

        # Some upstream responses carry HTTP 200 / statusCodeValue 200 but the body itself is an error JSON.
        if status_code == 200:
            inner = envelope.get("body")
            if isinstance(inner, str):
                try:
                    parsed = json.loads(inner)
                except Exception:
                    parsed = None
            else:
                parsed = inner if isinstance(inner, dict) else None
            if isinstance(parsed, dict) and "code" in parsed and "message" in parsed and "choices" not in parsed:
                for frame in _error_frames(str(inner)):
                    yield frame
                break

        inner = envelope.get("body")
        if inner is None:
            continue
        if inner == "[DONE]":
            if pending_finish_choice is not None and last_chunk_template is not None:
                flush_chunk = dict(last_chunk_template)
                flush_chunk["choices"] = [pending_finish_choice]
                yield f"data: {json.dumps(flush_chunk)}\n\n"
                pending_finish_choice = None
            yield "data: [DONE]\n\n"
            break

        try:
            chunk = json.loads(inner) if isinstance(inner, str) else inner
        except Exception:
            yield f"data: {inner}\n\n"
            continue

        last_chunk_template = {
            "id": chunk.get("id", f"chatcmpl-{int(time.time())}"),
            "object": chunk.get("object", "chat.completion.chunk"),
            "created": chunk.get("created", int(time.time())),
            "model": chunk.get("model", ""),
        }

        choices = chunk.get("choices") or []
        usage = chunk.get("usage")

        if choices:
            first_choice = dict(choices[0])
            delta = first_choice.get("delta") or {}
            finish_reason = first_choice.get("finish_reason") or delta.get("finish_reason")
            if finish_reason and not first_choice.get("finish_reason"):
                first_choice["finish_reason"] = finish_reason
                chunk = dict(chunk)
                chunk["choices"] = [first_choice, *choices[1:]]

            valuable = bool(
                delta.get("content")
                or delta.get("tool_calls")
                or delta.get("reasoning_content")
                or delta.get("role")
            )
            if finish_reason and not valuable:
                pending_finish_choice = first_choice
                continue

            if pending_finish_choice is not None:
                flush_chunk = dict(last_chunk_template)
                flush_chunk["choices"] = [pending_finish_choice]
                yield f"data: {json.dumps(flush_chunk)}\n\n"
                pending_finish_choice = None

            yield f"data: {json.dumps(chunk)}\n\n"
        elif usage is not None:
            if pending_finish_choice is not None:
                coalesced_chunk = dict(last_chunk_template)
                coalesced_chunk["choices"] = [pending_finish_choice]
                coalesced_chunk["usage"] = usage
                yield f"data: {json.dumps(coalesced_chunk)}\n\n"
                pending_finish_choice = None
            else:
                yield f"data: {json.dumps(chunk)}\n\n"

    if pending_finish_choice is not None and last_chunk_template is not None:
        flush_chunk = dict(last_chunk_template)
        flush_chunk["choices"] = [pending_finish_choice]
        yield f"data: {json.dumps(flush_chunk)}\n\n"
        yield "data: [DONE]\n\n"


async def accumulate_sse_response(byte_lines: AsyncIterator[str]) -> dict[str, Any]:
    full_content: list[str] = []
    full_reasoning: list[str] = []
    tool_calls_map: dict[int, dict[str, Any]] = {}
    finish_reason = None
    usage = None
    response_id = f"chatcmpl-{int(time.time())}"
    model = ""
    created = int(time.time())

    async for chunk_str in unwrap_sse_stream(byte_lines):
        chunk_str = chunk_str.strip()
        if not chunk_str.startswith("data:"):
            continue
        data_body = chunk_str[len("data:") :].strip()
        if data_body == "[DONE]":
            break

        try:
            chunk = json.loads(data_body)
        except Exception:
            continue

        if "error" in chunk:
            return chunk

        if "id" in chunk:
            response_id = chunk["id"]
        if "model" in chunk and chunk["model"]:
            model = chunk["model"]
        if "created" in chunk:
            created = chunk["created"]
        if "usage" in chunk and chunk["usage"]:
            usage = chunk["usage"]

        choices = chunk.get("choices") or []
        for choice in choices:
            delta = choice.get("delta") or {}
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            elif delta.get("finish_reason"):
                finish_reason = delta["finish_reason"]

            if "content" in delta and delta["content"]:
                full_content.append(delta["content"])
            if "reasoning_content" in delta and delta["reasoning_content"]:
                full_reasoning.append(delta["reasoning_content"])

            if "tool_calls" in delta and delta["tool_calls"]:
                for tc in delta["tool_calls"]:
                    idx = tc.get("index", 0)
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {
                            "id": tc.get("id", ""),
                            "type": tc.get("type", "function"),
                            "function": {
                                "name": tc.get("function", {}).get("name", ""),
                                "arguments": tc.get("function", {}).get("arguments", ""),
                            },
                        }
                    else:
                        existing = tool_calls_map[idx]
                        if tc.get("id"):
                            existing["id"] = tc["id"]
                        if tc.get("type"):
                            existing["type"] = tc["type"]
                        fn = tc.get("function", {})
                        if fn.get("name"):
                            existing["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            existing["function"]["arguments"] += fn["arguments"]

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(full_content) if full_content else None,
    }
    if full_reasoning:
        message["reasoning_content"] = "".join(full_reasoning)

    if tool_calls_map:
        sorted_indices = sorted(tool_calls_map.keys())
        message["tool_calls"] = [tool_calls_map[i] for i in sorted_indices]
        if finish_reason in (None, "stop"):
            finish_reason = "tool_calls"

    choice_obj: dict[str, Any] = {
        "index": 0,
        "message": message,
        "finish_reason": finish_reason or "stop",
    }

    return {
        "id": response_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [choice_obj],
        "usage": usage
        or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
