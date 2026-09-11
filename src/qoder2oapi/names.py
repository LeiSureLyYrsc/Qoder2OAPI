from __future__ import annotations

# Internal catalog keys → public OpenAI-style ids / display names (Qoder CN).
PUBLIC_BY_KEY: dict[str, dict[str, str]] = {
    "auto": {"id": "auto", "name": "Auto"},
    "lite": {"id": "lite", "name": "Lite"},
    "efficient": {"id": "efficient", "name": "Efficient"},
    "performance": {"id": "performance", "name": "Performance"},
    "ultimate": {"id": "ultimate", "name": "Ultimate"},
    "qmodel_preview": {"id": "qwen3.8-max", "name": "Qwen 3.8 Max"},
    "qmodel_latest": {"id": "qwen3.7-max", "name": "Qwen 3.7 Max"},
    "qmodel": {"id": "qwen3.7-plus", "name": "Qwen 3.7 Plus"},
    "qfmodel": {"id": "qwen3.7-flash", "name": "Qwen 3.7 Flash"},
    "q36fmodel": {"id": "qwen3.6-flash", "name": "Qwen 3.6 Flash"},
    "dmodel": {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro"},
    "dfmodel": {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash"},
    "gm53model": {"id": "glm-5.3", "name": "GLM 5.3"},
    "gm51model": {"id": "glm-5.2", "name": "GLM 5.2"},
    "kmodel": {"id": "kimi-k2.7-code", "name": "Kimi K2.7 Code"},
    "kmodel_latest": {"id": "kimi-k3", "name": "Kimi K3"},
    "mmodel": {"id": "minimax-m2.7", "name": "MiniMax M2.7"},
    "cmodel": {"id": "cantus", "name": "Cantus"},
}

# Public / alias ids → internal catalog keys.
ALIAS_TO_KEY: dict[str, str] = {
    "qoder-cn": "auto",
    "auto": "auto",
    "lite": "lite",
    "efficient": "efficient",
    "performance": "performance",
    "ultimate": "ultimate",
    "qwen3.8-max": "qmodel_preview",
    "qwen3.8-max-preview": "qmodel_preview",
    "qwen-3.8-max": "qmodel_preview",
    "qwen3.7-max": "qmodel_latest",
    "qwen-3.7-max": "qmodel_latest",
    "qwen3.7-plus": "qmodel",
    "qwen-3.7-plus": "qmodel",
    "qwen3.6-plus": "qmodel",
    "qwen3.7-flash": "qfmodel",
    "qwen-3.7-flash": "qfmodel",
    "qwen3.6-flash": "q36fmodel",
    "deepseek-v4-pro": "dmodel",
    "deepseek-v4-flash": "dfmodel",
    "glm-5.3": "gm53model",
    "glm-5.2": "gm51model",
    "glm-5.1": "gm51model",
    "kimi-k2.7-code": "kmodel",
    "kimi-k2.6": "kmodel",
    "kimi-k3": "kmodel_latest",
    "minimax-m2.7": "mmodel",
    "minimax-m3": "mmodel",
    "cantus": "cmodel",
}

for _key, _info in PUBLIC_BY_KEY.items():
    ALIAS_TO_KEY.setdefault(_key, _key)
    ALIAS_TO_KEY.setdefault(_info["id"], _key)


def strip_model_prefix(name: str) -> str:
    raw = str(name or "").strip()
    lowered = raw.lower()
    prefixes = ("qoder/", "qoder-cn/", "qoder" + "cn/")
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return raw[len(prefix) :]
    return raw


def public_id_for_key(key: str) -> str:
    info = PUBLIC_BY_KEY.get(key)
    return info["id"] if info else key


def public_name_for_key(key: str, fallback: str | None = None) -> str:
    info = PUBLIC_BY_KEY.get(key)
    if info:
        return info["name"]
    return fallback or key


def alias_to_internal_key(name: str) -> str:
    cleaned = strip_model_prefix(name)
    return ALIAS_TO_KEY.get(cleaned) or ALIAS_TO_KEY.get(cleaned.lower()) or cleaned
