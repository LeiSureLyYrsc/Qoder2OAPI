from copy import deepcopy
import json
import time
from typing import Any

from qoder2oapi.constants import MODEL_LIST_ALGO_URL, MODEL_LIST_URL
from qoder2oapi.cosy import build_cosy_headers
from qoder2oapi.http import get_http_client
from qoder2oapi.token_store import token_store

_THINKING_ORDER = ["none", "low", "medium", "high", "xhigh", "max"]


class CatalogManager:
    def __init__(self) -> None:
        self.raw_models: list[dict[str, Any]] = []
        self.models_by_key: dict[str, dict[str, Any]] = {}
        self.last_updated: float = 0.0

    def get_model(self, key: str) -> dict[str, Any] | None:
        clean_key = key.removeprefix("qoder/")
        return self.models_by_key.get(clean_key)

    def extract_context_tiers(self, model_data: dict[str, Any]) -> list[dict[str, Any]]:
        context_config = model_data.get("context_config") or model_data.get("contextConfig") or []
        tiers = []
        if isinstance(context_config, list):
            for item in context_config:
                if not isinstance(item, dict):
                    continue
                token_count = (
                    item.get("tokenCount")
                    or item.get("token_count")
                    or item.get("max_input_tokens")
                    or item.get("maxInputTokens")
                    or item.get("contextLength")
                    or item.get("context_length")
                    or 0
                )
                name = (
                    item.get("name")
                    or item.get("label")
                    or item.get("display_name")
                    or item.get("displayName")
                    or item.get("key")
                    or item.get("id")
                    or str(token_count)
                )
                is_default = bool(
                    item.get("isDefault") or item.get("is_default") or item.get("default", False)
                )
                tiers.append(
                    {
                        "token_count": int(token_count),
                        "name": str(name),
                        "is_default": is_default,
                        "raw": item,
                    }
                )
        tiers.sort(key=lambda x: x["token_count"])
        return tiers

    def get_max_context_length(self, model_data: dict[str, Any]) -> int:
        tiers = self.extract_context_tiers(model_data)
        if tiers:
            return tiers[-1]["token_count"]
        return int(
            model_data.get("max_input_tokens")
            or model_data.get("maxInputTokens")
            or model_data.get("context_length")
            or 32768
        )

    def get_max_output_tokens(self, model_data: dict[str, Any]) -> int:
        return int(
            model_data.get("max_output_tokens")
            or model_data.get("maxOutputTokens")
            or 32768
        )

    def determine_thinking_levels(self, model_data: dict[str, Any]) -> list[str]:
        model_config = model_data.get("model_config") or model_data.get("modelConfig") or {}
        for k in ["thinking_levels", "reasoning_effort", "thinking", "reasoning", "effort"]:
            val = model_config.get(k)
            if isinstance(val, list):
                levels = [str(x).lower() for x in val if str(x).lower() in _THINKING_ORDER]
                if levels:
                    levels.sort(key=lambda x: _THINKING_ORDER.index(x))
                    return levels
        is_reasoning = bool(model_data.get("is_reasoning") or model_data.get("isReasoning"))
        if is_reasoning:
            return ["low", "medium", "high"]
        return []

    def get_default_thinking(self, model_data: dict[str, Any]) -> str | None:
        levels = self.determine_thinking_levels(model_data)
        if not levels:
            return None
        return levels[-1]

    async def fetch_models(self) -> list[dict[str, Any]]:
        usable = [acc for acc in token_store.list_accounts() if acc.enabled and not acc.skip_auth and acc.access_token]
        record = usable[0] if usable else token_store.load_token()
        if not record:
            return self.raw_models

        creds = record.model_dump()
        client = get_http_client()
        headers = build_cosy_headers(b"", MODEL_LIST_URL, creds)
        headers["Accept"] = "application/json"

        resp = None
        try:
            resp = await client.get(MODEL_LIST_URL, headers=headers)
        except Exception:
            resp = None

        if not resp or resp.status_code != 200:
            headers_algo = build_cosy_headers(b"", MODEL_LIST_ALGO_URL, creds)
            headers_algo["Accept"] = "application/json"
            try:
                resp = await client.get(MODEL_LIST_ALGO_URL, headers=headers_algo)
            except Exception:
                resp = None

        if resp and resp.status_code == 200:
            data = resp.json()
            chat_list = _extract_chat_list(data)

            if chat_list:
                self.raw_models = chat_list
                self.models_by_key = {
                    item["key"]: item for item in chat_list if isinstance(item, dict) and "key" in item
                }
                self.last_updated = time.time()

        return self.raw_models

    def prepare_model_config(
        self,
        base_model_data: dict[str, Any],
        override_reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        config_copy = deepcopy(base_model_data)
        config_copy["key"] = base_model_data.get("key") or config_copy.get("key")
        max_context = self.get_max_context_length(base_model_data)
        config_copy["max_input_tokens"] = max_context
        config_copy["context_length"] = max_context

        effort = override_reasoning_effort or self.get_default_thinking(base_model_data)
        if effort:
            config_copy["reasoning_effort"] = effort
        return config_copy


def _extract_chat_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    body = data.get("body") if "body" in data else data.get("data")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            body = None
    if isinstance(body, dict):
        chat = body.get("chat")
        if isinstance(chat, list):
            return [item for item in chat if isinstance(item, dict)]
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    chat = data.get("chat")
    if isinstance(chat, list):
        return [item for item in chat if isinstance(item, dict)]
    return []


catalog_manager = CatalogManager()
