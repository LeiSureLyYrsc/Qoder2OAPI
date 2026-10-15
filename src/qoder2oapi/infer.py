from collections.abc import AsyncIterator
import json
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

from qoder2oapi.catalog import catalog_manager
from qoder2oapi.constants import CHAT_URL
from qoder2oapi.cosy import build_cosy_headers
from qoder2oapi.encoding import qoder_encode_body
from qoder2oapi.http import get_http_client
from qoder2oapi.models import ChatCompletionRequest
from qoder2oapi.names import alias_to_internal_key
from qoder2oapi.pool import is_zero_cost_model, pool
from qoder2oapi.refresh import ensure_fresh, needs_refresh
from qoder2oapi.runtime_settings import runtime_settings
from qoder2oapi.sse import accumulate_sse_response, unwrap_sse_stream
from qoder2oapi.token_store import token_store  # noqa: F401  (test isolation patches this)
from qoder2oapi.translator import translate_openai_to_qoder


async def _line_generator(response: httpx.Response) -> AsyncIterator[str]:
    async for line in response.aiter_lines():
        yield line


async def _peek_first_sse_item(
    stream_iter: AsyncIterator[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Peek the first non-empty SSE event line(s) and determine if upstream returned an error.

    Returns:
        (error_info, buffered_chunks)
        where error_info is a dict containing error details if an upstream error was detected,
        or None if normal stream content,
        and buffered_chunks is the list of unparsed/parsed chunks already read that must be yielded.
    """
    buffered: list[str] = []
    async for chunk in stream_iter:
        buffered.append(chunk)
        line = chunk.strip()
        if not line:
            continue
        if not line.startswith("data:"):
            # Not an SSE data frame; keep peeking
            continue

        raw_data = line[len("data:") :].strip()
        if not raw_data or raw_data == "[DONE]":
            # Normal stream or immediate DONE
            return None, buffered

        try:
            envelope = json.loads(raw_data)
        except Exception:
            # Not JSON; let stream pass through
            return None, buffered

        # 1. Direct error field in chunk
        if isinstance(envelope, dict) and "error" in envelope:
            err_obj = envelope["error"]
            return {"error": err_obj, "raw": raw_data}, buffered

        # 2. Envelope status code (e.g. statusCodeValue != 200)
        status_code = envelope.get("statusCodeValue", 200) if isinstance(envelope, dict) else 200
        if status_code != 200:
            err_body = envelope.get("body") or envelope.get("message") or "Upstream error"
            return {"statusCodeValue": status_code, "body": err_body, "raw": raw_data}, buffered

        # 3. HTTP 200 / statusCodeValue 200 but body contains error JSON (e.g. {"code": 112...})
        inner = envelope.get("body") if isinstance(envelope, dict) else None
        if isinstance(inner, str):
            try:
                parsed_inner = json.loads(inner)
            except Exception:
                parsed_inner = None
        else:
            parsed_inner = inner if isinstance(inner, dict) else None

        if isinstance(parsed_inner, dict) and "choices" not in parsed_inner:
            if "code" in parsed_inner or "error" in parsed_inner or _is_quota_error(parsed_inner):
                return {"error": parsed_inner, "body": inner, "raw": raw_data}, buffered

        # Normal first frame with choices/content
        return None, buffered

    return None, buffered


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


def resolve_model_dispatch(model: str) -> tuple[str, bool]:
    found = catalog_manager.get_model(model)
    key = str(found.get("key") or "") if isinstance(found, dict) else ""
    if not key:
        key = alias_to_internal_key(model)
    mode = runtime_settings.model_dispatch.get(key) or runtime_settings.dispatch_mode
    include_quota = (mode == "poll_include_quota") or is_zero_cost_model(model) or is_zero_cost_model(key)
    if mode == "failover":
        return "failover", include_quota
    return "poll", include_quota


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
    strategy, include_quota = resolve_model_dispatch(request.model)
    candidates = await pool.candidate_accounts(strategy, include_quota)
    if not candidates:
        raise HTTPException(status_code=401, detail="No usable Qoder account. Please log in or check quotas.")

    client = get_http_client()
    last_status_code = 401
    last_error_code: int | None = None

    def _advance_on_failure(account_id: str) -> None:
        if strategy == "failover":
            pool.advance_active(account_id)

    for candidate in candidates:
        if not pool.eligible(candidate, include_quota):
            continue
        account = candidate

        if needs_refresh(account):
            account = await ensure_fresh(account)
            if not pool.eligible(account, include_quota):
                _advance_on_failure(account.id)
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
                if pool.eligible(account, include_quota):
                    encoded_body_bytes, payload, model_data = _encode_chat_body(request, account)
                    headers = _chat_headers(encoded_body_bytes, account, payload, model_data)
                    req2 = client.build_request("POST", CHAT_URL, content=encoded_body_bytes, headers=headers)
                    upstream_resp = await client.send(req2, stream=True)
                    last_status_code = upstream_resp.status_code
                    if upstream_resp.status_code in (401, 403):
                        await upstream_resp.aclose()
                        pool.mark_skip_auth(account.id, f"HTTP {upstream_resp.status_code}")
                        _advance_on_failure(account.id)
                        continue
                else:
                    pool.mark_skip_auth(account.id, "Token refresh failed on 401/403")
                    _advance_on_failure(account.id)
                    continue
            else:
                pool.mark_skip_auth(account.id, f"HTTP {upstream_resp.status_code}")
                _advance_on_failure(account.id)
                continue

        # Handle 429
        if upstream_resp.status_code == 429:
            await upstream_resp.aclose()
            pool.mark_rate_limited(account.id, duration_seconds=30)
            _advance_on_failure(account.id)
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
            # Peek first SSE item to verify upstream didn't send an SSE error frame
            raw_stream = _line_generator(upstream_resp)
            error_info, buffered = await _peek_first_sse_item(raw_stream)

            if error_info is not None:
                await upstream_resp.aclose()
                err_content = error_info.get("error") or error_info.get("body") or error_info
                code = _extract_error_code(err_content) or error_info.get("statusCodeValue")
                if code is not None:
                    last_error_code = code
                    last_status_code = code

                if code == 429:
                    pool.mark_rate_limited(account.id, duration_seconds=30)
                    _advance_on_failure(account.id)
                    continue
                elif code in (401, 403):
                    pool.mark_skip_auth(account.id, f"SSE error code: {code}")
                    _advance_on_failure(account.id)
                    continue
                elif code == 112 or _is_quota_error(err_content):
                    pool.mark_skip_quota(account.id, f"SSE quota error: {err_content}")
                    _advance_on_failure(account.id)
                    continue
                else:
                    return _error_response(
                        str(err_content.get("message") if isinstance(err_content, dict) else err_content),
                        code or 500,
                        code=code,
                    )

            # Peek succeeded: upstream sent normal content
            pool.record_success(account.id, strategy)

            async def combined_lines():
                for b_chunk in buffered:
                    yield b_chunk
                async for chunk in raw_stream:
                    yield chunk

            async def response_stream():
                try:
                    async for chunk in unwrap_sse_stream(combined_lines()):
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
                    last_error_code = code
                    last_status_code = code

                if code == 429:
                    pool.mark_rate_limited(account.id, duration_seconds=30)
                    _advance_on_failure(account.id)
                    continue
                elif code in (401, 403):
                    pool.mark_skip_auth(account.id, f"Accumulate error {code}")
                    _advance_on_failure(account.id)
                    continue
                elif code == 112 or _is_quota_error(err_body):
                    pool.mark_skip_quota(account.id, f"Accumulate quota error: {err_body}")
                    _advance_on_failure(account.id)
                    continue

                if code is not None:
                    return _error_response(
                        str(err_body.get("message") if isinstance(err_body, dict) else err_body),
                        code,
                        code=code,
                    )
                return _error_response(
                    str(err_body.get("message") if isinstance(err_body, dict) else err_body),
                    500,
                    code=None,
                )

            pool.record_success(account.id, strategy)
            return JSONResponse(content=result)

    # If all usable accounts exhausted, return the last upstream HTTP status code.
    detail = f"All accounts failed (last upstream HTTP {last_status_code})"
    if last_error_code is not None:
        detail = f"All accounts failed (last upstream error code {last_error_code})"
    raise HTTPException(status_code=last_status_code, detail=detail)
