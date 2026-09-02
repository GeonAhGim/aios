"""encryption.py 포맷 단위 테스트 — `aios1$<kid>$<b64>` + 레거시 복호.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-31
(+ §2 101행). 핵심 불변: 기존 `legacy_encrypt`가 만든 토큰이 새
`decrypt(token, ring)`으로 그대로(평문 왕복) 복호돼야 한다.
"""
from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag

from src.core.security.encryption import decrypt, encrypt, legacy_decrypt, legacy_encrypt
from src.core.security.key_ring import KeyRing, UnknownKeyIdError

KEY_V1 = "11" * 32
KEY_V2 = "22" * 32
LEGACY_KEY_HEX = "33" * 32


def _ring(active_kid: str = "v2") -> KeyRing:
    return KeyRing.from_env(
        "PAPER",
        env={
            "CREDENTIAL_ENCRYPTION_KEY": LEGACY_KEY_HEX,
            "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1},v2:{KEY_V2}",
            "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": active_kid,
        },
    )


def test_encrypt_writes_new_format_with_active_kid() -> None:
    ring = _ring(active_kid="v2")
    token = encrypt("hello", ring)
    prefix, kid, _body = token.split("$", 2)
    assert prefix == "aios1"
    assert kid == "v2"


def test_encrypt_decrypt_round_trip_with_active_kid() -> None:
    ring = _ring(active_kid="v1")
    token = encrypt("secret-value", ring)
    assert decrypt(token, ring) == "secret-value"


def test_decrypt_uses_kid_embedded_in_token_not_active_kid() -> None:
    """v1으로 쓴 뒤 active_kid가 v2로 바뀌어도(회전) 여전히 복호돼야 함."""
    ring_v1_active = _ring(active_kid="v1")
    token = encrypt("rotate-me", ring_v1_active)

    ring_v2_active = _ring(active_kid="v2")
    assert decrypt(token, ring_v2_active) == "rotate-me"


def test_legacy_token_round_trips_through_new_decrypt() -> None:
    """레거시 토큰(접두 없음) -> 평문: 기존 legacy_encrypt 산출물이 새
    decrypt(token, ring)로 그대로 복호돼야 한다(저장된 기존 암호문 보존)."""
    legacy_token = legacy_encrypt("legacy-plaintext", LEGACY_KEY_HEX)
    assert "$" not in legacy_token

    ring = _ring()
    assert decrypt(legacy_token, ring) == "legacy-plaintext"


def test_legacy_encrypt_decrypt_round_trip_unchanged() -> None:
    token = legacy_encrypt("still-works", LEGACY_KEY_HEX)
    assert legacy_decrypt(token, LEGACY_KEY_HEX) == "still-works"


def test_decrypt_unknown_kid_raises() -> None:
    ring = _ring()
    token = "aios1$v99$" + encrypt("x", ring).split("$", 2)[2]
    with pytest.raises(UnknownKeyIdError):
        decrypt(token, ring)


def test_decrypt_rejects_kid_swap_because_aad_binds_kid() -> None:
    """토큰의 kid 필드를 다른 유효 kid로 바꿔치기하면 AAD 불일치로 인증 실패."""
    ring = _ring()
    token = encrypt("bound-to-v1", ring)
    _prefix, kid, body = token.split("$", 2)
    assert kid == ring.active_kid
    other_kid = "v1" if kid != "v1" else "v2"
    swapped = f"aios1${other_kid}${body}"
    with pytest.raises(InvalidTag):
        decrypt(swapped, ring)


def test_decrypt_rejects_tampered_ciphertext() -> None:
    ring = _ring()
    token = encrypt("tamper-check", ring)
    prefix, kid, body = token.split("$", 2)
    tampered_body = body[:-4] + ("A" if body[-4] != "A" else "B") + body[-3:]
    with pytest.raises((InvalidTag, ValueError)):
        decrypt(f"{prefix}${kid}${tampered_body}", ring)
