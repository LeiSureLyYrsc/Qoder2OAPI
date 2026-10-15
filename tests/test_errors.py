import pytest
from qoder2oapi.errors import (
    KNOWN_ERRORS,
    get_error_detail,
    is_auth_error,
    is_quota_error,
    is_rate_limit,
)


def test_known_errors_completeness():
    expected_codes = [
        101, 102, 103, 104, 105, 107, 108, 109, 110, 112, 113, 114, 115,
        116, 117, 118, 119, 122, 124, 125, 130, 131, 406, 408, 416, 418,
        429, 430, 500, 504, 601, 10408, 10605, 47902, 47903, 48716, 80411,
        80412, 90000, 100400, 100401, 100403, 100406,
        "job_token_invalid", "auth.failed", "quota.exceeded", "ratelimit.exceeded",
    ]
    for code in expected_codes:
        assert code in KNOWN_ERRORS, f"Missing error code {code}"
        err_type, desc, hint = KNOWN_ERRORS[code]
        assert err_type
        assert desc
        assert hint


def test_get_error_detail_known():
    detail = get_error_detail(112)
    assert detail["code"] == 112
    assert detail["type"] == "quota_exceeded"
    assert "已达点数（Credits）使用上限" in detail["description"]
    assert "账号额度耗尽" in detail["hint"]
    assert "已达点数（Credits）使用上限" in detail["formatted_message"]
    assert "[账号额度耗尽，请充值或切换可用账号]" in detail["formatted_message"]

    # String version of numeric code
    detail_str = get_error_detail("112")
    assert detail_str["code"] == 112
    assert detail_str["type"] == "quota_exceeded"


def test_get_error_detail_with_raw_message():
    detail = get_error_detail(105, raw_message="Token expired on server")
    assert detail["code"] == 105
    assert detail["type"] == "authentication_error"
    assert "(Token expired on server)" in detail["formatted_message"]
    assert "[Token 已失效，需重新登录]" in detail["formatted_message"]


def test_get_error_detail_unknown():
    detail = get_error_detail(999999, raw_message="Something unexpected")
    assert detail["code"] == 999999
    assert detail["type"] == "qoder_error"
    assert detail["description"] == "Something unexpected"
    assert detail["hint"] == ""
    assert detail["formatted_message"] == "Something unexpected"


def test_is_quota_error():
    # By int code
    assert is_quota_error(112) is True
    assert is_quota_error(110) is True
    assert is_quota_error(119) is True
    assert is_quota_error("quota.exceeded") is True

    # By string representation
    assert is_quota_error("112") is True

    # By dict
    assert is_quota_error({"code": 112}) is True
    assert is_quota_error({"code": "115"}) is True
    assert is_quota_error({"message": "user has insufficient quota remaining"}) is True
    assert is_quota_error({"error": {"code": 112, "message": "out of credits"}}) is True

    # Non-quota
    assert is_quota_error(101) is False
    assert is_quota_error({"code": 101, "message": "invalid signature"}) is False


def test_is_auth_error():
    # By code
    assert is_auth_error(105) is True
    assert is_auth_error(401) is True
    assert is_auth_error("job_token_invalid") is True
    assert is_auth_error("auth.failed") is True

    # By dict
    assert is_auth_error({"code": 105}) is True
    assert is_auth_error({"error": {"code": "job_token_invalid"}}) is True
    assert is_auth_error({"message": "authentication failed: token expired"}) is True

    # Non-auth
    assert is_auth_error(112) is False


def test_is_rate_limit():
    assert is_rate_limit(429) is True
    assert is_rate_limit("429") is True
    assert is_rate_limit("ratelimit.exceeded") is True
    assert is_rate_limit({"code": 429}) is True
    assert is_rate_limit({"message": "Too Many Requests - rate limit exceeded"}) is True
    assert is_rate_limit(105) is False
