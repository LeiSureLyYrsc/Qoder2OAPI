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
from qoder2oapi.pool import pool
from qoder2oapi.refresh import ensure_fresh
from qoder2oapi.sse import accumulate_sse_response, unwrap_sse_stream
from qoder2oapi.token_store import token_store


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


async def execute_infer(
    payload: dict[str, Any],
    model_data: dict[str, Any],
    stream: bool,
) -> StreamingResponse | JSONResponse:
    usable_accounts = pool.peek_usable()
    if not usable_accounts:
        raise HTTPException(status_code=401, detail="No usable Qoder account. Please log in or check quotas.")

    raw_json_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded_body_str = qoder_encode_body(raw_json_bytes)
    encoded_body_bytes = encoded_body_str.encode("latin1")

    client = get_http_client()
    all_auth_errors = True

    for _ in range(len(usable_accounts)):
        account = await pool.next_account()
        if not account:
            break

        if account.kind == "pat":
            account = await ensure_fresh(account)
            if not pool.usable(account):
                continue

        creds = account.model_dump()
        headers = build_cosy_headers(encoded_body_bytes, CHAT_URL, creds)
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "text/event-stream"
        headers["Accept-Encoding"] = "identity"

        model_key = str(
            model_data.get("key", payload.get("chat_context", {}).get("extra", {}).get("modelConfig", {}).get("key", ""))
        )
        headers["X-Model-Key"] = model_key

        model_source = str(
            model_data.get("source")
            or payload.get("model_config", {}).get("source")
            or "system"
        )
        headers["X-Model-Source"] = model_source

        req = client.build_request(
            "POST",
            CHAT_URL,
            content=encoded_body_bytes,
            headers=headers,
        )
        upstream_resp = await client.send(req, stream=True)

        # Handle 401 / 403
        if upstream_resp.status_code in (401, 403):
            await upstream_resp.aclose()
            if account.kind == "pat":
                account = await ensure_fresh(account)
                if pool.usable(account):
                    # retry once with refreshed account
                    creds = account.model_dump()
                    headers = build_cosy_headers(encoded_body_bytes, CHAT_URL, creds)
                    headers["Content-Type"] = "application/json"
                    headers["Accept"] = "text/event-stream"
                    headers["Accept-Encoding"] = "identity"
                    headers["X-Model-Key"] = model_key
                    headers["X-Model-Source"] = model_source
                    req2 = client.build_request("POST", CHAT_URL, content=encoded_body_bytes, headers=headers)
                    upstream_resp = await client.send(req2, stream=True)
                    if upstream_resp.status_code in (401, 403):
                        await upstream_resp.aclose()
                        pool.mark_skip_auth(account.id, f"HTTP {upstream_resp.status_code}")
                        continue
                else:
                    pool.mark_skip_auth(account.id, "PAT refresh failed on 401/403")
                    continue
            else:
                pool.mark_skip_auth(account.id, f"HTTP {upstream_resp.status_code}")
                continue

        # Handle 429
        if upstream_resp.status_code == 429:
            all_auth_errors = False
            await upstream_resp.aclose()
            pool.mark_skip_quota(account.id, "HTTP 429 Too Many Requests")
            continue

        if upstream_resp.status_code != 200:
            err_body = await upstream_resp.aread()
            await upstream_resp.aclose()
            return JSONResponse(
                status_code=upstream_resp.status_code,
                content={
                    "error": {
                        "message": f"Upstream error {upstream_resp.status_code}: {err_body.decode('utf-8', errors='replace')}",
                        "type": "upstream_error",
                        "code": upstream_resp.status_code,
                    }
                },
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
                if _is_quota_error(result.get("error")):
                    all_auth_errors = False
                    pool.mark_skip_quota(account.id, f"Accumulate quota error: {result.get('error')}")
                    continue
                return JSONResponse(status_code=500, content=result)
            return JSONResponse(content=result)

    # If all usable accounts exhausted
    status_code = 401 if all_auth_errors else 429
    detail = "All accounts failed with authentication errors" if all_auth_errors else "All accounts exceeded quota or rate limits"
    raise HTTPException(status_code=status_code, detail=detail)
