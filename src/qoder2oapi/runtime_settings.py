import json
from pathlib import Path
from typing import Any

from qoder2oapi.config import data_dir_path

# Global account dispatch strategies. "poll" spreads requests across usable
# accounts; "failover" sticks to the active account until it fails.
DISPATCH_MODES = ("poll", "failover")

# Per-model overrides. "inherit" means fall back to the global dispatch_mode.
# "poll_include_quota" is for zero-cost models and additionally allows
# quota-marked (skip_quota) accounts. skip_auth accounts are never allowed.
MODEL_DISPATCH_MODES = ("inherit", "poll", "poll_include_quota", "failover")

DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 1,
    "auto_mark_quota": False,
    "auto_mark_auth": True,
    "dispatch_mode": "poll",
    "model_dispatch": {},
}


class RuntimeSettings:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or data_dir_path
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_file = self.data_dir / "settings.json"
        self._data: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.load()

    @staticmethod
    def _sanitize_model_dispatch(value: Any) -> dict[str, str]:
        result: dict[str, str] = {}
        if not isinstance(value, dict):
            return result
        for key, mode in value.items():
            name = str(key).strip()
            if name and mode in MODEL_DISPATCH_MODES and mode != "inherit":
                result[name] = mode
        return result

    def load(self) -> dict[str, Any]:
        if not self.settings_file.exists():
            self._data = dict(DEFAULT_SETTINGS)
            return self.get_settings()
        try:
            content = self.settings_file.read_text(encoding="utf-8")
            raw = json.loads(content)
            if isinstance(raw, dict):
                quota_value = raw.get("auto_mark_quota", DEFAULT_SETTINGS["auto_mark_quota"])
                auth_value = raw.get("auto_mark_auth", DEFAULT_SETTINGS["auto_mark_auth"])
                dispatch_value = raw.get("dispatch_mode", DEFAULT_SETTINGS["dispatch_mode"])
                self._data = {
                    "version": int(raw.get("version", DEFAULT_SETTINGS["version"])),
                    "auto_mark_quota": quota_value if isinstance(quota_value, bool) else False,
                    "auto_mark_auth": auth_value if isinstance(auth_value, bool) else True,
                    "dispatch_mode": dispatch_value if dispatch_value in DISPATCH_MODES else "poll",
                    "model_dispatch": self._sanitize_model_dispatch(raw.get("model_dispatch")),
                }
            else:
                self._data = dict(DEFAULT_SETTINGS)
        except Exception:
            self._data = dict(DEFAULT_SETTINGS)
        return self.get_settings()

    def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": int(self._data.get("version", 1)),
            "auto_mark_quota": bool(self._data.get("auto_mark_quota", False)),
            "auto_mark_auth": bool(self._data.get("auto_mark_auth", True)),
            "dispatch_mode": self.dispatch_mode,
            "model_dispatch": self.model_dispatch,
        }
        temp_file = self.settings_file.with_name(f"{self.settings_file.name}.tmp")
        temp_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_file.replace(self.settings_file)

    @property
    def auto_mark_quota(self) -> bool:
        return bool(self._data.get("auto_mark_quota", False))

    @auto_mark_quota.setter
    def auto_mark_quota(self, value: bool) -> None:
        self._data["auto_mark_quota"] = bool(value)
        self.save()

    @property
    def auto_mark_auth(self) -> bool:
        return bool(self._data.get("auto_mark_auth", True))

    @auto_mark_auth.setter
    def auto_mark_auth(self, value: bool) -> None:
        self._data["auto_mark_auth"] = bool(value)
        self.save()

    @property
    def dispatch_mode(self) -> str:
        mode = self._data.get("dispatch_mode", "poll")
        return mode if mode in DISPATCH_MODES else "poll"

    @dispatch_mode.setter
    def dispatch_mode(self, value: str) -> None:
        self._data["dispatch_mode"] = value if value in DISPATCH_MODES else "poll"
        self.save()

    @property
    def model_dispatch(self) -> dict[str, str]:
        return dict(self._data.get("model_dispatch", {}))

    def set_model_dispatch(self, key: str, mode: str) -> None:
        name = str(key or "").strip()
        if not name:
            return
        mapping = dict(self._data.get("model_dispatch", {}))
        if mode in MODEL_DISPATCH_MODES and mode != "inherit":
            mapping[name] = mode
        else:
            mapping.pop(name, None)
        self._data["model_dispatch"] = mapping
        self.save()

    @property
    def version(self) -> int:
        return int(self._data.get("version", 1))

    def get_settings(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "auto_mark_quota": self.auto_mark_quota,
            "auto_mark_auth": self.auto_mark_auth,
            "dispatch_mode": self.dispatch_mode,
            "model_dispatch": self.model_dispatch,
        }

    def update(
        self,
        auto_mark_quota: bool | None = None,
        auto_mark_auth: bool | None = None,
        dispatch_mode: str | None = None,
    ) -> dict[str, Any]:
        if auto_mark_quota is not None:
            self._data["auto_mark_quota"] = bool(auto_mark_quota)
        if auto_mark_auth is not None:
            self._data["auto_mark_auth"] = bool(auto_mark_auth)
        if dispatch_mode is not None and dispatch_mode in DISPATCH_MODES:
            self._data["dispatch_mode"] = dispatch_mode
        self.save()
        return self.get_settings()


runtime_settings = RuntimeSettings()
