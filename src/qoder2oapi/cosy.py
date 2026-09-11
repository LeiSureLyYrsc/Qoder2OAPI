import base64
import hashlib
import json
import time
import uuid
from urllib.parse import urlparse
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sym_padding

from qoder2oapi.constants import (
    CLIENT_TYPE,
    COSY_VERSION,
    DATA_POLICY,
    IDE_VERSION,
    LOGIN_VERSION,
    MACHINE_OS,
    MACHINE_TYPE,
    RSA_PUBLIC_KEY,
)


def _pad_pkcs7(data: bytes, block_size: int = 128) -> bytes:
    padder = sym_padding.PKCS7(block_size).padder()
    return padder.update(data) + padder.finalize()


def _encrypt_aes_128_cbc(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    padded = _pad_pkcs7(plaintext)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _encrypt_rsa_pkcs1(public_key_pem: str, data: bytes) -> bytes:
    pub_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    return pub_key.encrypt(data, padding.PKCS1v15())


def build_cosy_headers(
    body: bytes,
    request_url: str,
    creds: dict[str, str],
    aes_key: str | None = None,
    timestamp: str | None = None,
    request_id: str | None = None,
) -> dict[str, str]:
    if aes_key is None:
        aes_key = str(uuid.uuid4())[:16]  # hyphenated 16 chars, matches qodercli
    if timestamp is None:
        timestamp = str(int(time.time()))
    if request_id is None:
        request_id = str(uuid.uuid4())

    aes_key_bytes = aes_key.encode("utf-8")

    user_info = {
        "uid": creds.get("user_id", ""),
        "security_oauth_token": creds.get("access_token", ""),
        "name": creds.get("name", ""),
        "aid": "",
        "email": creds.get("email", ""),
    }
    user_info_bytes = json.dumps(user_info, separators=(",", ":")).encode("utf-8")
    aes_encrypted = _encrypt_aes_128_cbc(aes_key_bytes, aes_key_bytes, user_info_bytes)
    info_b64 = base64.b64encode(aes_encrypted).decode("latin1")

    cosy_key_bytes = _encrypt_rsa_pkcs1(RSA_PUBLIC_KEY, aes_key_bytes)
    cosy_key = base64.b64encode(cosy_key_bytes).decode("latin1")

    payload_json = {
        "version": "v1",
        "requestId": request_id,
        "info": info_b64,
        "cosyVersion": COSY_VERSION,
        "ideVersion": IDE_VERSION,
    }
    payload_b64 = base64.b64encode(
        json.dumps(payload_json, separators=(",", ":")).encode("utf-8")
    ).decode("latin1")

    parsed = urlparse(request_url)
    sig_path = parsed.path
    if sig_path.startswith("/algo"):
        sig_path = sig_path[len("/algo") :]

    body_latin1 = body.decode("latin1")
    sig_input = f"{payload_b64}\n{cosy_key}\n{timestamp}\n{body_latin1}\n{sig_path}"
    sig = hashlib.md5(sig_input.encode("latin1")).hexdigest()

    body_hash = hashlib.md5(body).hexdigest()
    machine_id = creds.get("machine_id") or str(uuid.uuid4())

    headers = {
        "Authorization": f"Bearer COSY.{payload_b64}.{sig}",
        "Cosy-Key": cosy_key,
        "Cosy-User": creds.get("user_id", ""),
        "Cosy-Date": timestamp,
        "Cosy-Version": COSY_VERSION,
        "Cosy-Machineid": machine_id,
        "Cosy-Machinetoken": machine_id,
        "Cosy-Machinetype": MACHINE_TYPE,
        "Cosy-Machineos": MACHINE_OS,
        "Cosy-Clienttype": CLIENT_TYPE,
        "Cosy-Clientip": "127.0.0.1",
        "Cosy-Bodyhash": body_hash,
        "Cosy-Bodylength": str(len(body)),
        "Cosy-Sigpath": sig_path,
        "Cosy-Data-Policy": DATA_POLICY,
        "Cosy-Organization-Id": "",
        "Cosy-Organization-Tags": "",
        "Login-Version": LOGIN_VERSION,
        "X-Request-Id": request_id,
    }
    return headers
