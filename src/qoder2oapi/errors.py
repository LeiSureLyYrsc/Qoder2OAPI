from typing import Any
import json

# Comprehensive mapping of known Qoder CLI error codes:
# code -> (type, description, hint)
KNOWN_ERRORS: dict[int | str, tuple[str, str, str]] = {
    101: ("invalid_request_error", "签名无效，请重试", "请求签名校验失败"),
    102: ("service_unavailable", "请求超时，请稍后重试", "网关服务请求超时"),
    103: ("invalid_request_error", "重复请求", "请求 ID 冲突"),
    104: ("permission_error", "账户存在异常活动，已被限制访问", "账号触发平台风控保护，已被限制"),
    105: ("authentication_error", "登录状态已过期，请重新登录", "Token 已失效，需重新登录"),
    107: ("permission_error", "企业已配置 IP 白名单拦截", "需配置企业 IP 白名单"),
    108: ("permission_error", "当前账号暂无使用权限", "联系管理员开通权限"),
    109: ("permission_error", "应用已被停用", "企业管理员已停用此应用"),
    110: ("quota_exceeded", "已达每日问答次数上限", "今日调用次数耗尽，请明日再试或换号"),
    112: ("quota_exceeded", "已达点数（Credits）使用上限", "账号额度耗尽，请充值或切换可用账号"),
    113: ("quota_exceeded", "已达到使用上限", "账号资源使用已达上限"),
    114: ("quota_exceeded", "免费试用账户数量已超限", "设备免费试用账号数达到上限"),
    115: ("quota_exceeded", "已达到基础模型的每月使用上限", "Lite 基础模型月度额度耗尽"),
    116: ("quota_exceeded", "团队管理员点数已达上限", "团队点数耗尽，等待下个计费周期重置"),
    117: ("quota_exceeded", "团队成员点数已达上限", "点数耗尽，请联系管理员"),
    118: ("quota_exceeded", "个人点数已达上限", "新注册赠送点数待到账或额度耗尽"),
    119: ("quota_exceeded", "今日该模型已达免费额度", "今日免费额度耗尽，请明日再试"),
    122: ("quota_exceeded", "当前团队/组织已达到额度上限", "计费组配额超限"),
    124: ("permission_error", "服务账号不存在", "Service Account 不存在"),
    125: ("permission_error", "服务账号已被禁用", "Service Account 已禁用"),
    130: ("permission_error", "请求被企业安全策略拦截", "企业策略合规拦截"),
    131: ("service_unavailable", "企业策略检查服务暂时不可用", "策略服务不可用"),
    406: ("content_filter_error", "对话包含敏感内容", "触发内容安全过滤，请修改提示词或开启新对话"),
    408: ("service_unavailable", "回复响应超时", "上游模型响应超时"),
    416: ("content_filter_error", "触发模型安全策略，请求被拒绝", "内容可能违规，已被模型安全系统拒绝"),
    418: ("invalid_request_error", "作为智能编程助手无法回答此类问题", "请求超出编程助手支持范畴"),
    429: ("rate_limit_exceeded", "当前模型繁忙或请求频率过高", "触发并发或频率限流，正在冷却自动重试"),
    430: ("invalid_request_error", "当前功能在旧版本中不再支持", "协议或功能版本不匹配"),
    500: ("server_error", "上游服务发生异常", "Qoder 服务端内部错误"),
    504: ("service_unavailable", "服务网关超时", "上游网关连接超时"),
    601: ("content_filter_error", "命中企业智能问答安全过滤规则", "对话被企业级安全审查过滤"),
    10408: ("service_unavailable", "网络连接中断或无法连接上游服务", "检查网络与代理设置"),
    10605: ("service_unavailable", "模型推理排队失败", "排队超时或失败，请稍后重试或切换模型"),
    47902: ("context_length_exceeded", "已达到最大会话轮次上限", "上下文轮次超限，请开启新会话"),
    47903: ("context_length_exceeded", "已达到最大输出内容长度上限", "输出 token 超过最大限制"),
    48716: ("permission_error", "被前置/后置 Hook 拦截", "Hook 拦截任务执行"),
    80411: ("context_length_exceeded", "输入内容过长，超出上下文窗口", "Prompt 长度超限，请简化后重试"),
    80412: ("invalid_request_error", "包含过多图片或多模态文档", "多模态媒体文件数量超限"),
    90000: ("invalid_request_error", "所选模型不支持图片或多模态输入", "请选择视觉/多模态模型"),
    100400: ("server_error", "自定义模型服务异常", "BYOK 自定义服务报错"),
    100401: ("authentication_error", "自定义模型鉴权失败", "检查 BYOK API Key 配置"),
    100403: ("invalid_request_error", "自定义模型已被删除", "BYOK 模型不可用"),
    100406: ("permission_error", "企业管理员已禁用自定义模型", "BYOK 功能被管理员禁用"),
    "job_token_invalid": ("authentication_error", "Job Token 无效", "凭据失效，需重新兑换或刷新"),
    "auth.failed": ("authentication_error", "认证失败", "鉴权凭据错误"),
    "quota.exceeded": ("quota_exceeded", "配额已耗尽", "账号额度不足"),
    "ratelimit.exceeded": ("rate_limit_exceeded", "频率超限", "触发频率限制"),
}

QUOTA_CODES: set[int | str] = {
    code for code, info in KNOWN_ERRORS.items() if info[0] == "quota_exceeded"
}
AUTH_CODES: set[int | str] = {
    code for code, info in KNOWN_ERRORS.items() if info[0] == "authentication_error"
} | {401, 403, "401", "403"}
RATE_LIMIT_CODES: set[int | str] = {
    code for code, info in KNOWN_ERRORS.items() if info[0] == "rate_limit_exceeded"
}


def _normalize_code(code: int | str) -> int | str:
    if isinstance(code, int):
        return code
    code_str = str(code).strip()
    if code_str.isdigit() or (code_str.startswith("-") and code_str[1:].isdigit()):
        return int(code_str)
    return code_str


def get_error_detail(code: int | str, raw_message: str = "") -> dict[str, Any]:
    norm_code = _normalize_code(code)
    matched = KNOWN_ERRORS.get(norm_code)
    if matched is None and isinstance(norm_code, int):
        matched = KNOWN_ERRORS.get(str(norm_code))
    elif matched is None and isinstance(norm_code, str):
        try:
            matched = KNOWN_ERRORS.get(int(norm_code))
        except ValueError:
            pass

    if matched is not None:
        err_type, description, hint = matched
    else:
        err_type = "qoder_error"
        description = raw_message or f"Error {norm_code}"
        hint = ""

    parts = [description]
    if raw_message and raw_message != description:
        parts.append(f"({raw_message})")
    if hint:
        parts.append(f"[{hint}]")
    formatted_message = " ".join(parts).strip()

    return {
        "code": norm_code,
        "type": err_type,
        "description": description,
        "hint": hint,
        "formatted_message": formatted_message,
    }


def _extract_candidates(body: str | dict[str, Any] | int) -> tuple[int | str | None, str]:
    if isinstance(body, int):
        return body, ""
    if isinstance(body, str):
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                return _extract_candidates(parsed)
        except Exception:
            pass
        # Check if the string itself is a known code or integer code
        norm = _normalize_code(body)
        if norm in KNOWN_ERRORS or str(norm) in KNOWN_ERRORS:
            return norm, body
        return None, body
    if isinstance(body, dict):
        code = body.get("code")
        if code is None and isinstance(body.get("error"), dict):
            code = body["error"].get("code")
        msg = body.get("message") or ""
        if not msg and isinstance(body.get("error"), dict):
            msg = body["error"].get("message") or ""
        return code, str(msg)
    return None, str(body)


def is_quota_error(body: str | dict[str, Any] | int) -> bool:
    code, msg = _extract_candidates(body)
    if code is not None:
        norm = _normalize_code(code)
        if norm in QUOTA_CODES or str(norm) in QUOTA_CODES:
            return True

    text = f"{msg} {body if isinstance(body, str) else ''}".lower()
    quota_keywords = (
        "quota",
        "insufficient",
        "额度",
        "点数",
        "配额",
        "上限",
        "耗尽",
        "次数上限",
        "credits",
    )
    return any(k in text for k in quota_keywords)


def is_auth_error(body: str | dict[str, Any] | int) -> bool:
    code, msg = _extract_candidates(body)
    if code is not None:
        norm = _normalize_code(code)
        if norm in AUTH_CODES or str(norm) in AUTH_CODES:
            return True

    text = f"{msg} {body if isinstance(body, str) else ''}".lower()
    auth_keywords = (
        "unauthorized",
        "authentication",
        "token expired",
        "invalid token",
        "token 无效",
        "认证失败",
        "登录状态已过期",
        "鉴权失败",
        "token 已失效",
    )
    return any(k in text for k in auth_keywords)


def is_rate_limit(body: str | dict[str, Any] | int) -> bool:
    code, msg = _extract_candidates(body)
    if code is not None:
        norm = _normalize_code(code)
        if norm in RATE_LIMIT_CODES or str(norm) in RATE_LIMIT_CODES:
            return True

    text = f"{msg} {body if isinstance(body, str) else ''}".lower()
    rate_keywords = (
        "rate limit",
        "ratelimit",
        "too many requests",
        "频率过高",
        "频率超限",
        "限流",
        "模型繁忙",
    )
    return any(k in text for k in rate_keywords)
