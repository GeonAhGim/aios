"""봉투 암호화(envelope encryption) — 레코드별 DEK.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-32
(+ §2 102행, §3.6). 레코드마다 새 DEK(Data Encryption Key)를 생성해 본문을
암호화하고, DEK 자체는 `KeyRing`의 kid 키(KEK)로 한 번 더 감싼다(wrap).
회전(`rewrap`)은 DEK를 새 kid로 다시 감싸기만 하고 본문 재암호화는 하지
않는다 — 대용량 본문을 매 회전마다 다시 암호화하지 않기 위함(§9 PLT-32
decision).

AAD 설계: wrapped DEK는 `kid`를 AAD로 묶어 kid 바꿔치기(kid-confusion)를
인증 실패로 막는다. 본문은 레코드마다 새로 생성되는 DEK로만 암호화되므로
(다른 레코드와 키를 공유하지 않음) AAD가 필요 없다 — `rewrap`이 kid만
바꾸고 본문 nonce/ciphertext를 그대로 두므로, 본문 AAD를 kid에 묶으면
회전 직후 복호가 깨진다.
"""
from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict

from src.core.security.key_ring import KeyRing

_NONCE_SIZE = 12  # AES-GCM 표준 96비트 nonce
_DEK_SIZE = 32  # AES-256


class SealedRecord(BaseModel):
    """봉투 암호화 산출물. `wrapped_dek`는 `wrap_nonce(12) + wrap_ciphertext`
    연결(레거시 `encryption.py`와 동일한 nonce-prefix 관례). `nonce`/`ciphertext`는
    DEK로 암호화한 본문."""

    model_config = ConfigDict(frozen=True)

    kid: str
    wrapped_dek: bytes
    nonce: bytes
    ciphertext: bytes


def seal(plaintext: bytes, ring: KeyRing) -> SealedRecord:
    """새 DEK를 생성해 `plaintext`를 암호화하고, DEK를 `ring.active_kid`
    키로 감싼다."""
    kid = ring.active_kid
    dek = os.urandom(_DEK_SIZE)

    body_nonce = os.urandom(_NONCE_SIZE)
    ciphertext = AESGCM(dek).encrypt(body_nonce, plaintext, None)

    wrapped_dek = _wrap_dek(dek, kid, ring)
    return SealedRecord(kid=kid, wrapped_dek=wrapped_dek, nonce=body_nonce, ciphertext=ciphertext)


def open_(rec: SealedRecord, ring: KeyRing) -> bytes:
    """`rec.kid` 키로 DEK를 풀어(unwrap) 본문을 복호한다."""
    dek = _unwrap_dek(rec.wrapped_dek, rec.kid, ring)
    return AESGCM(dek).decrypt(rec.nonce, rec.ciphertext, None)


def rewrap(rec: SealedRecord, ring: KeyRing) -> SealedRecord:
    """DEK만 `ring.active_kid`로 재래핑한다 — 본문(nonce/ciphertext)은 그대로
    복사되어 재암호화 비용이 없다. 반환된 레코드는 새 kid로만 복호된다(옛
    kid로 감싼 `wrapped_dek`는 폐기된다)."""
    dek = _unwrap_dek(rec.wrapped_dek, rec.kid, ring)
    new_kid = ring.active_kid
    new_wrapped_dek = _wrap_dek(dek, new_kid, ring)
    return SealedRecord(
        kid=new_kid, wrapped_dek=new_wrapped_dek, nonce=rec.nonce, ciphertext=rec.ciphertext
    )


def _wrap_dek(dek: bytes, kid: str, ring: KeyRing) -> bytes:
    aad = kid.encode("ascii")
    wrap_nonce = os.urandom(_NONCE_SIZE)
    wrapped = AESGCM(ring.key(kid)).encrypt(wrap_nonce, dek, aad)
    return wrap_nonce + wrapped


def _unwrap_dek(wrapped_dek: bytes, kid: str, ring: KeyRing) -> bytes:
    aad = kid.encode("ascii")
    wrap_nonce, wrapped = wrapped_dek[:_NONCE_SIZE], wrapped_dek[_NONCE_SIZE:]
    return AESGCM(ring.key(kid)).decrypt(wrap_nonce, wrapped, aad)
