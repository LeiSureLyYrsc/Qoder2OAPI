import hashlib
import json
import time
from typing import Any
import uuid

from fastapi import HTTPException
from qoder2oapi.catalog import catalog_manager
from qoder2oapi.constants import (
    CLI_BUSINESS_PRODUCT,
    CLI_SESSION_TYPE,
    DESKTOP_APP_VERSION,
    DESKTOP_BUSINESS_PRODUCT,
    DESKTOP_SESSION_TYPE,
)
from qoder2oapi.identity import is_desktop
from qoder2oapi.models import ChatCompletionRequest


def _part_as_dict(part: Any) -> dict[str, Any] | None:
    if isinstance(part, dict):
        return part
    if hasattr(part, "model_dump"):
        dumped = part.model_dump()
        return dumped if isinstance(dumped, dict) else None
    return None


def _requested_context_length(request: ChatCompletionRequest) -> int | None:
    extras: dict[str, Any] = {}
    if isinstance(request.extra_body, dict):
        extras.update(request.extra_body)
    dumped = request.model_dump(exclude_unset=True)
    extras.update(dumped)
    for field in ("context_length", "max_input_tokens", "context_window"):
        raw = extras.get(field)
        if isinstance(raw, (int, float)) and raw > 0:
            return int(raw)
    return None


def _flatten_content(content: str | list[Any] | None) -> str | list[Any]:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        dict_parts = []
        has_image = False
        for p in content:
            as_dict = _part_as_dict(p)
            if as_dict is not None:
                dict_parts.append(as_dict)
                if as_dict.get("type") == "image_url" or "image_url" in as_dict:
                    has_image = True
            elif isinstance(p, str):
                dict_parts.append({"type": "text", "text": p})
        if has_image:
            return dict_parts
        texts = []
        for p in dict_parts:
            texts.append(str(p.get("text") or ""))
        return "".join(texts)
    return content


def translate_openai_to_qoder(
    request: ChatCompletionRequest,
    user_id: str,
    client: str = "cli",
) -> tuple[dict[str, Any], dict[str, Any]]:
    requested_key = request.model
    model_data = catalog_manager.get_model(requested_key)
    if not model_data:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{request.model}' not found in catalog. Available models may need refresh.",
        )
    model_key = str(model_data.get("key") or requested_key)

    system_msgs: list[str] = []
    filtered_messages: list[dict[str, Any]] = []
    last_user_text = ""

    for msg in request.messages:
        role = msg.role
        flat_content = _flatten_content(msg.content)
        if role == "system":
            if isinstance(flat_content, str):
                system_msgs.append(flat_content)
            elif flat_content:
                system_msgs.append(str(flat_content))
            continue

        item: dict[str, Any] = {"role": role, "content": flat_content}
        if msg.name:
            item["name"] = msg.name
        if msg.tool_calls:
            t_calls = []
            for tc in msg.tool_calls:
                if isinstance(tc, dict):
                    t_calls.append(tc)
                elif hasattr(tc, "model_dump"):
                    t_calls.append(getattr(tc, "model_dump")())
            item["tool_calls"] = t_calls
        if msg.tool_call_id:
            item["tool_call_id"] = msg.tool_call_id

        if role == "user" and isinstance(flat_content, str):
            last_user_text = flat_content

        filtered_messages.append(item)

    tools_list = request.tools or []

    client_effort = None
    if request.reasoning_effort:
        client_effort = request.reasoning_effort
    elif request.reasoning and isinstance(request.reasoning, dict):
        client_effort = request.reasoning.get("effort")

    chosen_effort = client_effort or catalog_manager.get_default_thinking(model_data)

    max_output = catalog_manager.get_max_output_tokens(model_data)
    req_max = request.max_tokens or request.max_completion_tokens
    chosen_max_tokens = min(req_max, max_output) if req_max else max_output

    requested_context = _requested_context_length(request)
    max_context = catalog_manager.resolve_context_length(model_data, requested_context)

    model_config_override = catalog_manager.prepare_model_config(
        model_data,
        override_reasoning_effort=chosen_effort,
        context_length=max_context,
    )

    parameters: dict[str, Any] = {
        "max_tokens": chosen_max_tokens,
        "context_length": max_context,
    }
    if request.temperature is not None:
        parameters["temperature"] = request.temperature
    if request.top_p is not None:
        parameters["top_p"] = request.top_p
    if chosen_effort:
        parameters["reasoning_effort"] = chosen_effort

    req_repr = f"{model_key}:{json.dumps(filtered_messages, sort_keys=True)}:{json.dumps(tools_list, sort_keys=True)}:{chosen_max_tokens}"
    hash_hex = hashlib.sha256(req_repr.encode("utf-8")).hexdigest()[:16]
    session_prefix = hashlib.sha256(f"{user_id}:{model_key}".encode("utf-8")).hexdigest()[:16]
    session_id = f"{session_prefix}-{uuid.uuid4()}"
    now_ms = int(time.time() * 1000)
    has_tools = bool(tools_list) or any(
        m.get("role") == "tool" or m.get("tool_calls") for m in filtered_messages
    )

    extra: dict[str, Any] = {
        "context": [],
        "modelConfig": {
            "key": model_key,
            "is_reasoning": bool(model_data.get("is_reasoning")),
        },
        "originalContent": last_user_text,
        "ideModelConfigOverride": {
            "max_input_tokens": max_context,
        },
    }
    if chosen_effort:
        extra["ideModelConfigOverride"]["reasoning_effort"] = chosen_effort

    chat_context: dict[str, Any] = {
        "chatPrompt": "",
        "imageUrls": None,
        "features": [],
        "text": last_user_text,
        "extra": extra,
    }

    system_text = "\n\n".join(system_msgs)
    desktop = is_desktop(client)
    session_type = DESKTOP_SESSION_TYPE if desktop else CLI_SESSION_TYPE
    business_product = DESKTOP_BUSINESS_PRODUCT if desktop else CLI_BUSINESS_PRODUCT
    business_version = DESKTOP_APP_VERSION if desktop else "1.0.0"

    payload: dict[str, Any] = {
        "request_id": str(uuid.uuid4()),
        "request_set_id": hash_hex,
        "chat_record_id": hash_hex,
        "session_id": session_id,
        "stream": True,
        "chat_task": "FREE_INPUT",
        "is_reply": not has_tools,
        "is_retry": False,
        "source": 1,
        "version": "3",
        "session_type": session_type,
        "agent_id": "agent_common",
        "task_id": "common",
        "code_language": "",
        "chat_prompt": "",
        "image_urls": None,
        "aliyun_user_type": "",
        "system": system_text,
        "messages": filtered_messages,
        "tools": tools_list,
        "parameters": parameters,
        "chat_context": chat_context,
        "model_config": model_config_override,
        "business": {
            "product": business_product,
            "version": business_version,
            "type": "agent",
            "stage": "start",
            "id": str(uuid.uuid4()),
            "name": (last_user_text[:30] if last_user_text else "chat"),
            "begin_at": now_ms,
        },
    }
    if request.tool_choice is not None:
        payload["tool_choice"] = request.tool_choice

    return payload, model_data
