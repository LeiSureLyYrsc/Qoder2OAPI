import hashlib
from qoder2oapi.cosy import build_cosy_headers


def test_build_cosy_headers():
    body = b'{"prompt":"test"}'
    url = "https://gateway.qoder.com.cn/algo/api/v2/service/pro/sse/agent_chat_generation?AgentId=agent_common"
    creds = {
        "user_id": "user_12345",
        "access_token": "token_abcde",
        "name": "Tester",
        "email": "tester@example.com",
        "machine_id": "mach-001",
    }
    aes_key = "12345678-1234-40"
    timestamp = "1700000000000"
    request_id = "req-1111-2222"

    headers = build_cosy_headers(
        body=body,
        request_url=url,
        creds=creds,
        aes_key=aes_key,
        timestamp=timestamp,
        request_id=request_id,
    )

    assert headers["Cosy-Sigpath"] == "/api/v2/service/pro/sse/agent_chat_generation"
    assert headers["Cosy-Bodyhash"] == hashlib.md5(body).hexdigest()
    assert headers["Cosy-Bodylength"] == str(len(body))
    assert headers["Cosy-User"] == "user_12345"
    assert headers["Cosy-Machineid"] == "mach-001"
    assert headers["Cosy-Machinetoken"] == "mach-001"
    assert headers["Cosy-Date"] == "1700000000000"
    assert headers["X-Request-Id"] == "req-1111-2222"
    assert headers["Authorization"].startswith("Bearer COSY.")

    auth_parts = headers["Authorization"].split(".")
    assert len(auth_parts) == 3
    assert auth_parts[0] == "Bearer COSY"
    assert len(auth_parts[2]) == 32


def test_build_cosy_headers_get_empty_body():
    url = "https://gateway.qoder.com.cn/api/v2/model/list"
    creds = {"user_id": "u1", "access_token": "t1"}
    headers = build_cosy_headers(b"", url, creds)
    assert headers["Cosy-Sigpath"] == "/api/v2/model/list"
    assert headers["Cosy-Bodyhash"] == hashlib.md5(b"").hexdigest()
    assert headers["Cosy-Bodylength"] == "0"
    assert headers["Cosy-Clienttype"] == "5"
    assert "Cosy-Business-Product" not in headers


def test_desktop_record_still_uses_cli_identity():
    url = "https://gateway.qoder.com.cn/algo/api/v2/service/pro/sse/agent_chat_generation"
    creds = {
        "user_id": "u1",
        "access_token": "t1",
        "machine_id": "m1",
        "client": "desktop",
    }
    headers = build_cosy_headers(b"{}", url, creds)
    assert headers["Cosy-Clienttype"] == "5"
    assert "Cosy-Business-Product" not in headers
    assert "User-Agent" not in headers
    assert headers["Cosy-Version"] == "1.0.0"
