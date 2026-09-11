import hmac
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from qoder2oapi.config import settings

security = HTTPBearer(auto_error=False)


def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Security(security),
) -> str:
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="Missing Bearer API key in Authorization header",
        )

    expected = settings.qoder2oapi_api_key.encode("utf-8")
    provided = credentials.credentials.encode("utf-8")

    if not hmac.compare_digest(expected, provided):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
        )

    return credentials.credentials
