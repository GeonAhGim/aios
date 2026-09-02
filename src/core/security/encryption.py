"""공용 대칭키 암호화 유틸 (AES-256-GCM).

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-31
(+ §2 101행). 키 버전(kid) 있는 포맷 `aios1$<kid>$<b64(nonce+ct)>` — AAD는
kid. 접두 없는 토큰은 레거시(`legacy_encrypt`로 생성)로 간주해 `kid="legacy"`,
AAD 없음으로 복호한다(당시 생성 방식과 동일해야 인증 태그가 맞음).

FD-11.2(MFA secret)/FD-12.1(거래소 자격증명)/FD-11.5(출금 화이트리스트
목적지)가 전부 동일한 애플리케이션 레벨 키 하나를 재사용한다(07번 §7.3
원칙) — 암호화 로직을 한 곳에 모아 각 사용처가 중복 구현하지 않게 한다.

기존 `encrypt(plaintext, key: str)`/`decrypt(token, key: str)`는
`legacy_encrypt`/`legacy_decrypt`로 개명됐다(PLT-31) — 저장된 기존
암호문은 이 두 함수가 만든 포맷 그대로이므로 절대 시그니처를 바꾸지 않는다.
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.core.security.key_ring import KeyRing

_NONCE_SIZE = 12  # AES-GCM 표준 96비트 nonce
_FORMAT_PREFIX = "aios1"
_LEGACY_KID = "legacy"


def _key_bytes(key: str) -> bytes:
    """CREDENTIAL_ENCRYPTION_KEY는 `openssl rand -hex 32`로 생성한 64자 hex
    문자열(.env.example 지시대로) — 32바이트 raw 키로 디코딩(AES-256)."""
    raw = bytes.fromhex(key)
    if len(raw) != 32:
        raise ValueError("CREDENTIAL_ENCRYPTION_KEY는 32바이트(hex 64자)여야 합니다.")
    return raw


def legacy_encrypt(plaintext: str, key: str) -> str:
    aesgcm = AESGCM(_key_bytes(key))
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def legacy_decrypt(token: str, key: str) -> str:
    raw = base64.b64decode(token)
    nonce, ciphertext = raw[:_NONCE_SIZE], raw[_NONCE_SIZE:]
    aesgcm = AESGCM(_key_bytes(key))
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")


def encrypt(plaintext: str, ring: KeyRing) -> str:
    """항상 신규 포맷 `aios1$<kid>$<b64>`로 쓴다(kid=ring.active_kid, AAD=kid)."""
    kid = ring.active_kid
    aad = kid.encode("ascii")
    aesgcm = AESGCM(ring.key(kid))
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)
    body = base64.b64encode(nonce + ciphertext).decode("ascii")
    return f"{_FORMAT_PREFIX}${kid}${body}"


def decrypt(token: str, ring: KeyRing) -> str:
    """`aios1$<kid>$<b64>`는 kid/AAD=kid로, 접두 없는 레거시 토큰은
    kid="legacy"/AAD 없음으로 복호(legacy_encrypt와 동일 조건)."""
    if token.startswith(f"{_FORMAT_PREFIX}$"):
        _, kid, body = token.split("$", 2)
        aad = kid.encode("ascii")
    else:
        kid, body = _LEGACY_KID, token
        aad = None
    raw = base64.b64decode(body)
    nonce, ciphertext = raw[:_NONCE_SIZE], raw[_NONCE_SIZE:]
    aesgcm = AESGCM(ring.key(kid))
    return aesgcm.decrypt(nonce, ciphertext, aad).decode("utf-8")
