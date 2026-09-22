from qoder2oapi.constants import CLIENT_CLI, CLIENT_DESKTOP


def normalize_client(value: str | None) -> str:
    raw = str(value or CLIENT_CLI).strip().lower()
    if raw in ("desktop", "app", "qoder-cn"):
        return CLIENT_DESKTOP
    return CLIENT_CLI


def is_desktop(value: str | None) -> bool:
    return normalize_client(value) == CLIENT_DESKTOP
