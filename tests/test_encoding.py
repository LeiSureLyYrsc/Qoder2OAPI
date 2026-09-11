import base64
from qoder2oapi.constants import CUSTOM_ALPHABET, STD_ALPHABET
from qoder2oapi.encoding import qoder_encode_body


def reference_encode(b: bytes) -> str:
    s = base64.b64encode(b).decode("latin1")
    n = len(s)
    a = n // 3
    rearranged = s[n - a :] + s[a : n - a] + s[:a]
    mapping = {c: CUSTOM_ALPHABET[i] for i, c in enumerate(STD_ALPHABET)}
    mapping["="] = "$"
    return "".join(mapping.get(c, c) for c in rearranged)


def test_encoding_basic():
    plain = b"Hello world! Testing Qoder body encoding."
    encoded = qoder_encode_body(plain)
    expected = reference_encode(plain)
    assert encoded == expected
    assert "$" in encoded or len(plain) % 3 == 0


def test_encoding_empty():
    assert qoder_encode_body(b"") == ""


def test_encoding_json_payload():
    payload = b'{"messages":[{"role":"user","content":"ping"}],"stream":true}'
    encoded = qoder_encode_body(payload)
    assert encoded == reference_encode(payload)
