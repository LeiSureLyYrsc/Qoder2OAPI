import json
from pathlib import Path
from typing import Any

from qoder2oapi.config import data_dir_path

DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 1,
    "auto_mark_quota": False,
    "auto_mark_auth": True,
}


class RuntimeSettings:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or data_dir_path
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_file = self.data_dir / "settings.json"
        self._data: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.load()

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
                self._data = {
                    "version": int(raw.get("version", DEFAULT_SETTINGS["version"])),
                    "auto_mark_quota": quota_value if isinstance(quota_value, bool) else False,
                    "auto_mark_auth": auth_value if isinstance(auth_value, bool) else True,
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
    def version(self) -> int:
        return int(self._data.get("version", 1))

    def get_settings(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "auto_mark_quota": self.auto_mark_quota,
            "auto_mark_auth": self.auto_mark_auth,
        }

    def update(
        self,
        auto_mark_quota: bool | None = None,
        auto_mark_auth: bool | None = None,
    ) -> dict[str, Any]:
        if auto_mark_quota is not None:
            self._data["auto_mark_quota"] = bool(auto_mark_quota)
        if auto_mark_auth is not None:
            self._data["auto_mark_auth"] = bool(auto_mark_auth)
        self.save()
        return self.get_settings()


runtime_settings = RuntimeSettings()
