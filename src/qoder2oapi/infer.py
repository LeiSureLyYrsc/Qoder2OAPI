from collections.abc import AsyncIterator
import json
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

from qoder2oapi.constants import CHAT_URL
from qoder2oapi.cosy import build_cosy_headers
from qoder2oapi.encoding import qoder_encode_body
from qoder2oapi.http import get_http_client
from qoder2oapi.models import ChatCompletionRequest
from qoder2oapi.pool import pool
from qoder2oapi.refresh import ensure_fresh, needs_refresh
from qoder2oapi.sse import accumulate_sse_response, unwrap_sse_stream
from qoder2oapi.token_store import token_store
from qoder2oapi.translator import translate_openai_to_qoder


async def _line_generator(response: httpx.Response) -> AsyncIterator[str]:
    async for line in response.aiter_lines():
        yield line


def _is_quota_error(err_content: Any) -> bool:
    if isinstance(err_content, dict):
        code = err_content.get("code")
        if code in (112, "112", 429, "429"):
            return True
        msg = str(err_content.get("message") or "").lower()
        if "quota" in msg or "insufficient" in msg:
            return True
        inner_err = err_content.get("error")
        if isinstance(inner_err, dict):
            return _is_quota_error(inner_err)
    elif isinstance(err_content, str):
        msg = err_content.lower()
        if "insufficient_quota" in msg or "quota exceeded" in msg or "quota" in msg:
            return True
    return False


def _extract_error_code(err_content: Any) -> int | None:
    if isinstance(err_content, dict):
        code = err_content.get("code")
        if isinstance(code, int):
            return code
        if isinstance(code, str) and code.isdigit():
            return int(code)
        inner_err = err_content.get("error")
        if isinstance(inner_err, dict):
            return _extract_error_code(inner_err)
    return None


def _encode_chat_body(request: ChatCompletionRequest, account) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    payload, model_data = translate_openai_to_qoder(
        request,
        user_id=account.user_id or "pool",
        client=account.client,
    )
    raw_json_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded_body_bytes = qoder_encode_body(raw_json_bytes).encode("latin1")
    return encoded_body_bytes, payload, model_data


def _chat_headers(encoded_body_bytes: bytes, account, payload: dict[str, Any], model_data: dict[str, Any]) -> dict[str, str]:
    creds = account.model_dump()
    headers = build_cosy_headers(encoded_body_bytes, CHAT_URL, creds)
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "text/event-stream"
    headers["Accept-Encoding"] = "identity"
    headers["X-Model-Key"] = str(
        model_data.get("key", payload.get("chat_context", {}).get("extra", {}).get("modelConfig", {}).get("key", ""))
    )
    headers["X-Model-Source"] = str(
        model_data.get("source")
        or payload.get("model_config", {}).get("source")
        or "system"
    )
    return headers


def _error_response(message: str, status_code: int, code: int | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": "upstream_error",
                "code": code if code is not None else status_code,
            }
        },
    )


async def execute_infer(
    request: ChatCompletionRequest,
    stream: bool,
) -> StreamingResponse | JSONResponse:
    usable_accounts = pool.peek_usable()
    if not usable_accounts:
        raise HTTPException(status_code=401, detail="No usable Qoder account. Please log in or check quotas.")

    client = get_http_client()
    last_status_code = 401
    last_error_code: int | None = None

    for _ in range(len(usable_accounts)):
        account = await pool.next_account()
        if not account:
            break

        if needs_refresh(account):
            account = await ensure_fresh(account)
            if not pool.usable(account):
                continue

        encoded_body_bytes, payload, model_data = _encode_chat_body(request, account)
        headers = _chat_headers(encoded_body_bytes, account, payload, model_data)

        req = client.build_request(
            "POST",
            CHAT_URL,
            content=encoded_body_bytes,
            headers=headers,
        )
        upstream_resp = await client.send(req, stream=True)
        last_status_code = upstream_resp.status_code

        # Handle 401 / 403
        if upstream_resp.status_code in (401, 403):
            await upstream_resp.aclose()
            if needs_refresh(account):
                account = await ensure_fresh(account, force=True)
                if pool.usable(account):
                    encoded_body_bytes, payload, model_data = _encode_chat_body(request, account)
                    headers = _chat_headers(encoded_body_bytes, account, payload, model_data)
                    req2 = client.build_request("POST", CHAT_URL, content=encoded_body_bytes, headers=headers)
                    upstream_resp = await client.send(req2, stream=True)
                    last_status_code = upstream_resp.status_code
                    if upstream_resp.status_code in (401, 403):
                        await upstream_resp.aclose()
                        pool.mark_skip_auth(account.id, f"HTTP {upstream_resp.status_code}")
                        continue
                else:
                    pool.mark_skip_auth(account.id, "Token refresh failed on 401/403")
                    continue
            else:
                pool.mark_skip_auth(account.id, f"HTTP {upstream_resp.status_code}")
                continue

        # Handle 429
        if upstream_resp.status_code == 429:
            await upstream_resp.aclose()
            pool.mark_skip_quota(account.id, "HTTP 429 Too Many Requests")
            continue

        if upstream_resp.status_code != 200:
            err_body = await upstream_resp.aread()
            await upstream_resp.aclose()
            return _error_response(
                f"Upstream error {upstream_resp.status_code}: {err_body.decode('utf-8', errors='replace')}",
                upstream_resp.status_code,
            )

        # If 200
        if stream:
            async def response_stream():
                try:
                    async for chunk in unwrap_sse_stream(_line_generator(upstream_resp)):
                        yield chunk
                finally:
                    await upstream_resp.aclose()

            return StreamingResponse(
                response_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            try:
                result = await accumulate_sse_response(_line_generator(upstream_resp))
            finally:
                await upstream_resp.aclose()

            if "error" in result:
                err_body = result.get("error")
                code = _extract_error_code(err_body)
                if code is not None:
                    # Proxy upstream error code as HTTP status; still mark quota-ish codes as skip.
                    if _is_quota_error(err_body):
                        pool.mark_skip_quota(account.id, f"Accumulate quota error: {err_body}")
                    return _error_response(
                        str(err_body.get("message") if isinstance(err_body, dict) else err_body),
                        code,
                        code=code,
                    )
                if _is_quota_error(err_body):
                    pool.mark_skip_quota(account.id, f"Accumulate quota error: {err_body}")
                    continue
                return _error_response(
                    str(err_body.get("message") if isinstance(err_body, dict) else err_body),
                    500,
                    code=None,
                )
            return JSONResponse(content=result)

    # If all usable accounts exhausted, return the last upstream HTTP status code.
    detail = f"All accounts failed (last upstream HTTP {last_status_code})"
    if last_error_code is not None:
        detail = f"All accounts failed (last upstream error code {last_error_code})"
    raise HTTPException(status_code=last_status_code, detail=detail)
