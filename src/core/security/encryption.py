"""공용 대칭키 암호화 유틸 (AES-256-GCM).

Spec: 07_logging_config_v1.3.md#§7.3 (CREDENTIAL_ENCRYPTION_KEY)

FD-11.2(MFA secret)/FD-12.1(거래소 자격증명)/FD-11.5(출금 화이트리스트
목적지)가 전부 동일한 애플리케이션 레벨 키 하나를 재사용한다(07번 §7.3
원칙) — 암호화 로직을 한 곳에 모아 각 사용처가 중복 구현하지 않게 한다.
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_SIZE = 12  # AES-GCM 표준 96비트 nonce


def _key_bytes(key: str) -> bytes:
    """CREDENTIAL_ENCRYPTION_KEY는 `openssl rand -hex 32`로 생성한 64자 hex
    문자열(.env.example 지시대로) — 32바이트 raw 키로 디코딩(AES-256)."""
    raw = bytes.fromhex(key)
    if len(raw) != 32:
        raise ValueError("CREDENTIAL_ENCRYPTION_KEY는 32바이트(hex 64자)여야 합니다.")
    return raw


def encrypt(plaintext: str, key: str) -> str:
    aesgcm = AESGCM(_key_bytes(key))
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt(token: str, key: str) -> str:
    raw = base64.b64decode(token)
    nonce, ciphertext = raw[:_NONCE_SIZE], raw[_NONCE_SIZE:]
    aesgcm = AESGCM(_key_bytes(key))
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
