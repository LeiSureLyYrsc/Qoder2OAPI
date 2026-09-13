from copy import deepcopy
import json
import re
import time
from typing import Any

from qoder2oapi.constants import MODEL_LIST_ALGO_URL, MODEL_LIST_URL
from qoder2oapi.cosy import build_cosy_headers
from qoder2oapi.http import get_http_client
from qoder2oapi.names import catalog_key_candidates
from qoder2oapi.refresh import ensure_fresh
from qoder2oapi.token_store import token_store

_THINKING_ORDER = ["none", "low", "medium", "high", "xhigh", "max"]


class CatalogManager:
    def __init__(self) -> None:
        self.raw_models: list[dict[str, Any]] = []
        self.models_by_key: dict[str, dict[str, Any]] = {}
        self.last_updated: float = 0.0

    def get_model(self, key: str) -> dict[str, Any] | None:
        for candidate in catalog_key_candidates(key):
            found = self.models_by_key.get(candidate)
            if found:
                return found
        return None

    def extract_context_tiers(self, model_data: dict[str, Any]) -> list[dict[str, Any]]:
        seen: dict[int, dict[str, Any]] = {}

        def add_tier(token_count: int, name: str = "", is_default: bool = False, raw: Any = None) -> None:
            if token_count <= 0:
                return
            existing = seen.get(token_count)
            if existing:
                existing["is_default"] = existing["is_default"] or is_default
                if name and not existing["name"]:
                    existing["name"] = name
                return
            seen[token_count] = {
                "token_count": token_count,
                "name": name or _format_token_label(token_count),
                "is_default": is_default,
                "raw": raw if isinstance(raw, dict) else {},
            }

        for source in (
            model_data.get("context_config"),
            model_data.get("contextConfig"),
            model_data.get("context_windows"),
            model_data.get("contextWindows"),
            model_data.get("available_context_windows"),
            model_data.get("availableContextWindows"),
            (model_data.get("model_config") or {}).get("context_config")
            if isinstance(model_data.get("model_config"), dict)
            else None,
        ):
            for item in _iter_context_entries(source):
                add_tier(
                    _parse_token_count(item),
                    _tier_name(item),
                    _tier_is_default(item),
                    item,
                )

        for field in (
            "max_input_tokens",
            "maxInputTokens",
            "context_length",
            "contextLength",
            "context_window",
            "contextWindow",
            "default_context_window",
            "defaultContextWindow",
        ):
            add_tier(_parse_token_count(model_data.get(field)))

        nested = model_data.get("model_config")
        if isinstance(nested, dict):
            for field in (
                "max_input_tokens",
                "maxInputTokens",
                "context_length",
                "context_window",
                "default_context_window",
            ):
                add_tier(_parse_token_count(nested.get(field)))

        return sorted(seen.values(), key=lambda x: x["token_count"])

    def get_max_context_length(self, model_data: dict[str, Any]) -> int:
        tiers = self.extract_context_tiers(model_data)
        if tiers:
            return tiers[-1]["token_count"]
        return int(model_data.get("max_input_tokens") or model_data.get("maxInputTokens") or 0) or 32768

    def get_min_context_length(self, model_data: dict[str, Any]) -> int:
        tiers = self.extract_context_tiers(model_data)
        if tiers:
            return tiers[0]["token_count"]
        return self.get_max_context_length(model_data)

    def resolve_context_length(self, model_data: dict[str, Any], requested: int | None = None) -> int:
        max_ctx = self.get_max_context_length(model_data)
        min_ctx = self.get_min_context_length(model_data)
        if requested is None or requested <= 0:
            return max_ctx
        return max(min_ctx, min(int(requested), max_ctx))

    def get_max_output_tokens(self, model_data: dict[str, Any]) -> int:
        return int(
            model_data.get("max_output_tokens")
            or model_data.get("maxOutputTokens")
            or 32768
        )

    def determine_thinking_levels(self, model_data: dict[str, Any]) -> list[str]:
        sources = [
            model_data,
            model_data.get("thinking_config") or model_data.get("thinkingConfig") or {},
            model_data.get("model_config") or model_data.get("modelConfig") or {},
        ]
        for source in sources:
            if not isinstance(source, dict):
                continue
            for k in (
                "thinking_levels",
                "supported_efforts",
                "reasoning_efforts",
                "reasoning_effort_levels",
                "efforts",
                "reasoning_effort",
                "thinking",
                "reasoning",
                "effort",
            ):
                levels = _normalize_thinking_levels(source.get(k))
                if levels:
                    return levels
        if bool(model_data.get("is_reasoning") or model_data.get("isReasoning")):
            return ["low", "medium", "high"]
        return []

    def get_default_thinking(self, model_data: dict[str, Any]) -> str | None:
        levels = self.determine_thinking_levels(model_data)
        if not levels:
            return None
        for source in (
            model_data,
            model_data.get("thinking_config") or {},
            model_data.get("thinkingConfig") or {},
            model_data.get("model_config") or {},
        ):
            if not isinstance(source, dict):
                continue
            for field in ("default_effort", "defaultEffort", "default_thinking"):
                val = str(source.get(field) or "").lower()
                if val in levels:
                    return val
        return levels[-1]

    async def fetch_models(self) -> list[dict[str, Any]]:
        usable = [acc for acc in token_store.list_accounts() if acc.enabled and not acc.skip_auth and acc.access_token]
        record = usable[0] if usable else token_store.load_token()
        if not record:
            return self.raw_models
        if record.kind == "pat":
            record = await ensure_fresh(record)
            if record.skip_auth or not record.user_id:
                return self.raw_models

        client = get_http_client()
        urls = (
            [f"{MODEL_LIST_ALGO_URL}?Encode=1", MODEL_LIST_ALGO_URL, MODEL_LIST_URL]
            if record.kind == "pat"
            else [MODEL_LIST_URL, f"{MODEL_LIST_ALGO_URL}?Encode=1", MODEL_LIST_ALGO_URL]
        )

        resp = await _fetch_model_response(client, urls, record.model_dump())
        if record.kind == "pat" and resp is not None and resp.status_code in (401, 403):
            record = await ensure_fresh(record, force=True)
            if not record.skip_auth and record.user_id:
                resp = await _fetch_model_response(client, urls, record.model_dump())

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
        context_length: int | None = None,
    ) -> dict[str, Any]:
        config_copy = deepcopy(base_model_data)
        config_copy["key"] = base_model_data.get("key") or config_copy.get("key")
        chosen_context = self.resolve_context_length(base_model_data, context_length)
        config_copy["max_input_tokens"] = chosen_context
        config_copy["context_length"] = chosen_context
        config_copy["context_window"] = chosen_context

        effort = override_reasoning_effort or self.get_default_thinking(base_model_data)
        if effort:
            config_copy["reasoning_effort"] = effort
        return config_copy


async def _fetch_model_response(client: Any, urls: list[str], creds: dict[str, Any]) -> Any:
    last_response = None
    for url in urls:
        headers = build_cosy_headers(b"", url, creds)
        headers["Accept"] = "application/json"
        try:
            response = await client.get(url, headers=headers)
        except Exception:
            continue
        last_response = response
        if response.status_code == 200:
            return response
        if response.status_code in (401, 403):
            return response
    return last_response


def _parse_token_count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    if isinstance(value, dict):
        for field in (
            "tokenCount",
            "token_count",
            "max_input_tokens",
            "maxInputTokens",
            "contextLength",
            "context_length",
            "contextWindow",
            "context_window",
            "tokens",
            "value",
        ):
            parsed = _parse_token_count(value.get(field))
            if parsed:
                return parsed
        return 0
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        match = re.match(r"^(\d+(?:\.\d+)?)\s*([kKmM])?$", text)
        if not match:
            digits = re.search(r"(\d+)", text)
            return int(digits.group(1)) if digits else 0
        number = float(match.group(1))
        suffix = (match.group(2) or "").lower()
        if suffix == "k":
            number *= 1000
        elif suffix == "m":
            number *= 1_000_000
        return int(number)
    return 0


def _tier_name(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    for field in ("name", "label", "display_name", "displayName", "key", "id"):
        val = item.get(field)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _tier_is_default(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    return bool(item.get("isDefault") or item.get("is_default") or item.get("default", False))


def _normalize_thinking_levels(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, list):
        candidates = value
    elif isinstance(value, dict):
        candidates = list(value.keys())
    else:
        return []
    for item in candidates:
        name = str(item).lower()
        if name in _THINKING_ORDER and name not in found:
            found.append(name)
    found.sort(key=lambda x: _THINKING_ORDER.index(x))
    return found


def _format_token_label(token_count: int) -> str:
    if token_count >= 1_000_000 and token_count % 1_000_000 == 0:
        return f"{token_count // 1_000_000}M"
    if token_count >= 1000 and token_count % 1000 == 0:
        return f"{token_count // 1000}K"
    return str(token_count)


_CONTEXT_LIST_KEYS = ("tiers", "windows", "options", "values", "items")
_CONTEXT_META_KEYS = {
    "token_count",
    "tokenCount",
    "max_input_tokens",
    "maxInputTokens",
    "context_length",
    "contextLength",
    "context_window",
    "contextWindow",
    "tokens",
    "value",
    "is_default",
    "isDefault",
    "default",
    "name",
    "label",
    "display_name",
    "displayName",
    "key",
    "id",
}


def _iter_context_entries(source: Any) -> list[Any]:
    if source is None:
        return []
    if isinstance(source, list):
        entries: list[Any] = []
        for item in source:
            entries.extend(_iter_context_entries(item))
        return entries
    if isinstance(source, dict):
        for field in _CONTEXT_LIST_KEYS:
            nested = source.get(field)
            if isinstance(nested, list):
                return _iter_context_entries(nested)
        mapped = _entries_from_keyed_windows(source)
        if mapped:
            return mapped
        return [source]
    return [source]


def _entries_from_keyed_windows(source: dict[str, Any]) -> list[Any]:
    if "token_count" in source or "tokenCount" in source:
        return []
    entries: list[Any] = []
    for key, value in source.items():
        if key in _CONTEXT_LIST_KEYS or key in _CONTEXT_META_KEYS:
            continue
        if isinstance(value, dict):
            parsed = _parse_token_count(value)
            if parsed:
                item = dict(value)
                item.setdefault("name", str(key))
                entries.append(item)
        else:
            parsed = _parse_token_count(value)
            if parsed:
                entries.append({"name": str(key), "token_count": parsed})
    return entries


def _models_from_list(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("key")]


def _models_from_scene_map(data: dict[str, Any]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    ordered_values = [data.get("chat"), data.get("default")]
    ordered_values.extend(
        value for name, value in data.items() if name not in ("chat", "default")
    )
    for value in ordered_values:
        for item in _models_from_list(value):
            key = str(item.get("key") or "")
            if key and key not in merged:
                merged[key] = item
    return list(merged.values())


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
        found = _models_from_scene_map(body)
        if found:
            return found
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    found = _models_from_scene_map(data)
    if found:
        return found
    return []


catalog_manager = CatalogManager()
