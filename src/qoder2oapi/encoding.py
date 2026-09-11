import base64
from qoder2oapi.constants import STD_ALPHABET, CUSTOM_ALPHABET

_TRANS_TABLE = str.maketrans(STD_ALPHABET + "=", CUSTOM_ALPHABET + "$")


def qoder_encode_body(body_bytes: bytes) -> str:
    b64_str = base64.b64encode(body_bytes).decode("latin1")
    n = len(b64_str)
    a = n // 3
    rearranged = b64_str[n - a :] + b64_str[a : n - a] + b64_str[:a]
    return rearranged.translate(_TRANS_TABLE)
