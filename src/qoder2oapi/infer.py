from collections.abc import AsyncIterator
import asyncio
import json
import logging
import time
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

from qoder2oapi.catalog import catalog_manager
from qoder2oapi.constants import CHAT_URL
from qoder2oapi.cosy import build_cosy_headers
from qoder2oapi.encoding import qoder_encode_body
from qoder2oapi.errors import (
    get_error_detail,
    is_auth_error,
    is_quota_error,
    is_rate_limit,
)
from qoder2oapi.http import get_http_client
from qoder2oapi.metrics import metrics_manager
from qoder2oapi.models import ChatCompletionRequest
from qoder2oapi.names import alias_to_internal_key
from qoder2oapi.pool import is_zero_cost_model, pool
from qoder2oapi.refresh import ensure_fresh, needs_refresh
from qoder2oapi.runtime_settings import runtime_settings
from qoder2oapi.sse import accumulate_sse_response, unwrap_sse_stream
from qoder2oapi.token_store import token_store  # noqa: F401  (test isolation patches this)
from qoder2oapi.translator import translate_openai_to_qoder

logger = logging.getLogger(__name__)


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
    return is_quota_error(err_content)


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


def _error_response(message: str, status_code: int, code: int | str | None = None) -> JSONResponse:
    err_detail = get_error_detail(code if code is not None else status_code, raw_message=message)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": err_detail["formatted_message"],
                "type": err_detail["type"],
                "param": None,
                "code": err_detail["code"],
            }
        },
    )


async def execute_infer(
    request: ChatCompletionRequest,
    stream: bool,
) -> StreamingResponse | JSONResponse:
    req_start_time = time.perf_counter()
    req_start_ts = int(time.time() * 1000)
    model_name = request.model

    strategy, include_quota = resolve_model_dispatch(request.model)
    candidates = await pool.candidate_accounts(strategy, include_quota)
    if not candidates:
        asyncio.create_task(
            metrics_manager.arecord_request(
                model=model_name,
                status_code=401,
                timestamp=req_start_ts,
                total_duration_ms=0.0,
                error_code="no_usable_account",
            )
        )
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
            err_detail = get_error_detail(upstream_resp.status_code)
            logger.warning(
                "Upstream auth error for account %s: HTTP %s - %s (%s)",
                account.id,
                upstream_resp.status_code,
                err_detail["description"],
                err_detail["hint"],
            )
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
                        err_detail2 = get_error_detail(upstream_resp.status_code)
                        pool.mark_skip_auth(
                            account.id,
                            f"HTTP {upstream_resp.status_code}: {err_detail2['description']} [{err_detail2['hint']}]",
                        )
                        _advance_on_failure(account.id)
                        continue
                else:
                    pool.mark_skip_auth(account.id, f"Token refresh failed on 401/403: {err_detail['description']} [{err_detail['hint']}]")
                    _advance_on_failure(account.id)
                    continue
            else:
                pool.mark_skip_auth(
                    account.id,
                    f"HTTP {upstream_resp.status_code}: {err_detail['description']} [{err_detail['hint']}]",
                )
                _advance_on_failure(account.id)
                continue

        # Handle 429
        if upstream_resp.status_code == 429:
            await upstream_resp.aclose()
            err_detail = get_error_detail(429)
            logger.warning(
                "Upstream rate limit for account %s: %s (%s)",
                account.id,
                err_detail["description"],
                err_detail["hint"],
            )
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

                raw_msg = str(err_content.get("message") if isinstance(err_content, dict) else err_content)
                err_detail = get_error_detail(code or 500, raw_message=raw_msg)
                logger.warning(
                    "SSE error from account %s (code %s): %s [%s]",
                    account.id,
                    code,
                    err_detail["description"],
                    err_detail["hint"],
                )

                if code == 429 or is_rate_limit(err_content):
                    pool.mark_rate_limited(account.id, duration_seconds=30)
                    _advance_on_failure(account.id)
                    continue
                elif code in (401, 403) or is_auth_error(err_content):
                    pool.mark_skip_auth(
                        account.id,
                        f"SSE auth error: {err_detail['description']} [{err_detail['hint']}]",
                    )
                    _advance_on_failure(account.id)
                    continue
                elif code == 112 or _is_quota_error(err_content):
                    pool.mark_skip_quota(
                        account.id,
                        f"SSE quota error: {err_detail['description']} [{err_detail['hint']}]",
                    )
                    _advance_on_failure(account.id)
                    continue
                else:
                    return _error_response(
                        raw_msg,
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
                first_token_time = None
                total_completion_tokens = 0
                total_prompt_tokens = 0
                total_cached_tokens = 0
                credits_deducted = 0.0

                try:
                    async for chunk in unwrap_sse_stream(combined_lines()):
                        if first_token_time is None:
                            first_token_time = time.perf_counter()
                        if isinstance(chunk, str) and "usage" in chunk:
                            try:
                                data_str = chunk.strip()
                                if data_str.startswith("data:"):
                                    data_str = data_str[5:].strip()
                                if data_str and not data_str.startswith("[DONE]"):
                                    parsed = json.loads(data_str)
                                    u = parsed.get("usage")
                                    if isinstance(u, dict):
                                        total_prompt_tokens = int(u.get("prompt_tokens") or total_prompt_tokens)
                                        total_completion_tokens = int(u.get("completion_tokens") or total_completion_tokens)
                                        details = u.get("prompt_tokens_details") or {}
                                        total_cached_tokens = int(details.get("cached_tokens") or total_cached_tokens)
                                        if "credits" in u:
                                            credits_deducted = float(u["credits"] or 0.0)
                            except Exception:
                                pass
                        yield chunk
                finally:
                    await upstream_resp.aclose()
                    t_end = time.perf_counter()
                    duration_ms = (t_end - req_start_time) * 1000.0
                    ttft_ms = ((first_token_time - req_start_time) * 1000.0) if first_token_time else duration_ms
                    gen_time_s = max(0.001, (t_end - (first_token_time or req_start_time)))
                    tps = total_completion_tokens / gen_time_s if total_completion_tokens > 0 else 0.0
                    asyncio.create_task(
                        metrics_manager.arecord_request(
                            model=model_name,
                            status_code=200,
                            account_id=account.id,
                            timestamp=req_start_ts,
                            ttft_ms=ttft_ms,
                            total_duration_ms=duration_ms,
                            tps=tps,
                            prompt_tokens=total_prompt_tokens,
                            cached_tokens=total_cached_tokens,
                            completion_tokens=total_completion_tokens,
                            credits_deducted=credits_deducted,
                        )
                    )

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

                raw_msg = str(err_body.get("message") if isinstance(err_body, dict) else err_body)
                err_detail = get_error_detail(code or 500, raw_message=raw_msg)
                logger.warning(
                    "Accumulate error from account %s (code %s): %s [%s]",
                    account.id,
                    code,
                    err_detail["description"],
                    err_detail["hint"],
                )

                if code == 429 or is_rate_limit(err_body):
                    pool.mark_rate_limited(account.id, duration_seconds=30)
                    _advance_on_failure(account.id)
                    continue
                elif code in (401, 403) or is_auth_error(err_body):
                    pool.mark_skip_auth(
                        account.id,
                        f"Accumulate auth error: {err_detail['description']} [{err_detail['hint']}]",
                    )
                    _advance_on_failure(account.id)
                    continue
                elif code == 112 or _is_quota_error(err_body):
                    pool.mark_skip_quota(
                        account.id,
                        f"Accumulate quota error: {err_detail['description']} [{err_detail['hint']}]",
                    )
                    _advance_on_failure(account.id)
                    continue

                if code is not None:
                    return _error_response(
                        raw_msg,
                        code,
                        code=code,
                    )
                return _error_response(
                    raw_msg,
                    500,
                    code=None,
                )

            pool.record_success(account.id, strategy)
            t_end = time.perf_counter()
            duration_ms = (t_end - req_start_time) * 1000.0
            usage = result.get("usage") if isinstance(result, dict) else {}
            p_toks = int(usage.get("prompt_tokens") or 0) if isinstance(usage, dict) else 0
            c_toks = int(usage.get("completion_tokens") or 0) if isinstance(usage, dict) else 0
            details = (usage.get("prompt_tokens_details") or {}) if isinstance(usage, dict) else {}
            cached_toks = int(details.get("cached_tokens") or 0)
            credits = float(usage.get("credits") or 0.0) if isinstance(usage, dict) else 0.0
            tps = c_toks / max(0.001, duration_ms / 1000.0) if c_toks > 0 else 0.0

            asyncio.create_task(
                metrics_manager.arecord_request(
                    model=model_name,
                    status_code=200,
                    account_id=account.id,
                    timestamp=req_start_ts,
                    ttft_ms=duration_ms,
                    total_duration_ms=duration_ms,
                    tps=tps,
                    prompt_tokens=p_toks,
                    cached_tokens=cached_toks,
                    completion_tokens=c_toks,
                    credits_deducted=credits,
                )
            )
            return JSONResponse(content=result)

    # If all usable accounts exhausted, return the last upstream HTTP status code.
    t_end = time.perf_counter()
    duration_ms = (t_end - req_start_time) * 1000.0
    asyncio.create_task(
        metrics_manager.arecord_request(
            model=model_name,
            status_code=last_status_code,
            timestamp=req_start_ts,
            total_duration_ms=duration_ms,
            error_code=str(last_error_code) if last_error_code is not None else None,
        )
    )
    detail = f"All accounts failed (last upstream HTTP {last_status_code})"
    if last_error_code is not None:
        detail = f"All accounts failed (last upstream error code {last_error_code})"
    raise HTTPException(status_code=last_status_code, detail=detail)
