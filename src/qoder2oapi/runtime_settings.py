import json
import re
from pathlib import Path
from typing import Any

from qoder2oapi.config import data_dir_path

TIME_REGEX = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 1,
    "auto_mark_quota": False,
    "auto_mark_auth": True,
    "auto_checkin": False,
    "checkin_time": "10:05",
}


def validate_checkin_time(time_str: str | None) -> bool:
    if not isinstance(time_str, str):
        return False
    return bool(TIME_REGEX.match(time_str.strip()))


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
                checkin_value = raw.get("auto_checkin", DEFAULT_SETTINGS["auto_checkin"])
                time_value = raw.get("checkin_time", DEFAULT_SETTINGS["checkin_time"])
                valid_time = time_value if validate_checkin_time(str(time_value)) else DEFAULT_SETTINGS["checkin_time"]
                self._data = {
                    "version": int(raw.get("version", DEFAULT_SETTINGS["version"])),
                    "auto_mark_quota": quota_value if isinstance(quota_value, bool) else False,
                    "auto_mark_auth": auth_value if isinstance(auth_value, bool) else True,
                    "auto_checkin": checkin_value if isinstance(checkin_value, bool) else False,
                    "checkin_time": str(valid_time),
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
            "auto_checkin": bool(self._data.get("auto_checkin", False)),
            "checkin_time": str(self._data.get("checkin_time", "10:05")),
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
    def auto_checkin(self) -> bool:
        return bool(self._data.get("auto_checkin", False))

    @auto_checkin.setter
    def auto_checkin(self, value: bool) -> None:
        self._data["auto_checkin"] = bool(value)
        self.save()

    @property
    def checkin_time(self) -> str:
        return str(self._data.get("checkin_time", "10:05"))

    @checkin_time.setter
    def checkin_time(self, value: str) -> None:
        if not validate_checkin_time(value):
            raise ValueError("Invalid checkin_time format, expected HH:MM (00:00 - 23:59)")
        self._data["checkin_time"] = value.strip()
        self.save()

    @property
    def version(self) -> int:
        return int(self._data.get("version", 1))

    def get_settings(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "auto_mark_quota": self.auto_mark_quota,
            "auto_mark_auth": self.auto_mark_auth,
            "auto_checkin": self.auto_checkin,
            "checkin_time": self.checkin_time,
        }

    def update(
        self,
        auto_mark_quota: bool | None = None,
        auto_mark_auth: bool | None = None,
        auto_checkin: bool | None = None,
        checkin_time: str | None = None,
    ) -> dict[str, Any]:
        if auto_mark_quota is not None:
            self._data["auto_mark_quota"] = bool(auto_mark_quota)
        if auto_mark_auth is not None:
            self._data["auto_mark_auth"] = bool(auto_mark_auth)
        if auto_checkin is not None:
            self._data["auto_checkin"] = bool(auto_checkin)
        if checkin_time is not None:
            if not validate_checkin_time(checkin_time):
                raise ValueError("Invalid checkin_time format, expected HH:MM (00:00 - 23:59)")
            self._data["checkin_time"] = checkin_time.strip()
        self.save()
        return self.get_settings()


runtime_settings = RuntimeSettings()
