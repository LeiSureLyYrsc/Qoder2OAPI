from qoder2oapi.constants import CLIENT_CLI


def normalize_client(value: str | None) -> str:
    raw = str(value or CLIENT_CLI).strip().lower()
    if raw in ("desktop", "app", "qoder-cn"):
        return CLIENT_CLI
    return CLIENT_CLI
