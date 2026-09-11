from __future__ import annotations

# China-region public ids are prefixed so a later international catalog can use another prefix.
PUBLIC_PREFIX = "cn"

# Internal catalog keys → public OpenAI-style ids / display names (Qoder CN client labels).
PUBLIC_BY_KEY: dict[str, dict[str, str]] = {
    "auto": {"id": "auto", "name": "Auto"},
    "lite": {"id": "lite", "name": "Lite"},
    "efficient": {"id": "efficient", "name": "Efficient"},
    "performance": {"id": "performance", "name": "Performance"},
    "ultimate": {"id": "ultimate", "name": "Ultimate"},
    "qmodel_38max": {"id": "qwen3.8-max", "name": "Qwen 3.8 Max"},
    "qmodel_preview": {"id": "qwen3.8-max", "name": "Qwen 3.8 Max"},
    "qmodel_latest": {"id": "qwen3.7-max", "name": "Qwen 3.7 Max"},
    "qmodel": {"id": "qwen3.7-plus", "name": "Qwen 3.7 Plus"},
    "qfmodel": {"id": "qwen3.8-flash", "name": "Qwen 3.8 Flash"},
    "q36fmodel": {"id": "qwen3.6-flash", "name": "Qwen 3.6 Flash"},
    "dmodel": {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro"},
    "dfmodel": {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash"},
    "gmodel": {"id": "glm-5.3", "name": "GLM 5.3"},
    "gfmodel": {"id": "glm-5.3-flash", "name": "GLM 5.3 Flash"},
    "gm53model": {"id": "glm-5.3", "name": "GLM 5.3"},
    "gm51model": {"id": "glm-5.2", "name": "GLM 5.2"},
    "kmodel": {"id": "kimi-k2.7-code", "name": "Kimi K2.7 Code"},
    "kmodel_latest": {"id": "kimi-k3", "name": "Kimi K3"},
    "mmodel": {"id": "minimax-m3", "name": "MiniMax M3"},
    "cmodel": {"id": "cantus", "name": "Cantus"},
}

# Public / alias ids → internal catalog keys. Unprefixed and prefixed forms both work.
ALIAS_TO_KEY: dict[str, str] = {
    "qoder-cn": "auto",
    "auto": "auto",
    "lite": "lite",
    "efficient": "efficient",
    "performance": "performance",
    "ultimate": "ultimate",
    "qwen3.8-max": "qmodel_38max",
    "qwen3.8-max-preview": "qmodel_38max",
    "qwen-3.8-max": "qmodel_38max",
    "qmodel_preview": "qmodel_38max",
    "qwen3.7-max": "qmodel_latest",
    "qwen-3.7-max": "qmodel_latest",
    "qwen3.7-plus": "qmodel",
    "qwen-3.7-plus": "qmodel",
    "qwen3.6-plus": "qmodel",
    "qwen3.8-flash": "qfmodel",
    "qwen-3.8-flash": "qfmodel",
    "qwen3.7-flash": "qfmodel",
    "qwen-3.7-flash": "qfmodel",
    "qwen3.6-flash": "q36fmodel",
    "deepseek-v4-pro": "dmodel",
    "deepseek-v4-flash": "dfmodel",
    "deepseek-flash": "dfmodel",
    "deepseek-v4.1-flash": "dfmodel",
    "deepseek-v4-1-flash": "dfmodel",
    "glm-5.3": "gmodel",
    "glm-5.3-flash": "gfmodel",
    "glm-5.2": "gm51model",
    "glm-5.1": "gm51model",
    "gm53model": "gmodel",
    "kimi-k2.7-code": "kmodel",
    "kimi-k2.6": "kmodel",
    "kimi-k3": "kmodel_latest",
    "minimax-m3": "mmodel",
    "minimax-m2.7": "mmodel",
    "cantus": "cmodel",
}

_PREFIXES = ("cn/", "qoder/", "qoder-cn/", "qoder" + "cn/")


def strip_model_prefix(name: str) -> str:
    raw = str(name or "").strip()
    lowered = raw.lower()
    for prefix in _PREFIXES:
        if lowered.startswith(prefix):
            return raw[len(prefix) :]
    return raw


def public_id_for_key(key: str) -> str:
    info = PUBLIC_BY_KEY.get(key)
    slug = info["id"] if info else key
    if "/" in slug:
        return slug
    return f"{PUBLIC_PREFIX}/{slug}"


def public_name_for_key(key: str, fallback: str | None = None) -> str:
    info = PUBLIC_BY_KEY.get(key)
    if info:
        return info["name"]
    return fallback or key


def alias_to_internal_key(name: str) -> str:
    cleaned = strip_model_prefix(name)
    return ALIAS_TO_KEY.get(cleaned) or ALIAS_TO_KEY.get(cleaned.lower()) or cleaned


def catalog_key_candidates(name: str) -> list[str]:
    cleaned = strip_model_prefix(name)
    primary = alias_to_internal_key(name)
    slug = PUBLIC_BY_KEY.get(primary, {}).get("id")
    candidates: list[str] = []
    for item in (primary, cleaned, name):
        if item and item not in candidates:
            candidates.append(item)
    if slug:
        for key, info in PUBLIC_BY_KEY.items():
            if info["id"] == slug and key not in candidates:
                candidates.append(key)
    return candidates


for _key, _info in PUBLIC_BY_KEY.items():
    ALIAS_TO_KEY.setdefault(_key, _key)
    ALIAS_TO_KEY.setdefault(_info["id"], _key)
