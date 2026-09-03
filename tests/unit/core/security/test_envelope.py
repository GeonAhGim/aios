"""envelope.py 단위 테스트 — seal/open/rewrap 왕복.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-32.
핵심 불변: seal→open은 항상 동일 평문을 돌려주고, rewrap 이후에는 새 kid로만
복호되며(옛 kid로 감싼 wrapped_dek는 폐기), 위·변조는 InvalidTag로 실패한다.
"""
from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag

from src.core.security.envelope import open_, rewrap, seal
from src.core.security.key_ring import KeyRing, UnknownKeyIdError

KEY_V1 = "11" * 32
KEY_V2 = "22" * 32


def _ring(active_kid: str = "v1") -> KeyRing:
    return KeyRing.from_env(
        "PAPER",
        env={
            "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1},v2:{KEY_V2}",
            "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": active_kid,
        },
    )


def _single_kid_ring(kid: str, hex_key: str) -> KeyRing:
    return KeyRing.from_env(
        "PAPER",
        env={
            "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"{kid}:{hex_key}",
            "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": kid,
        },
    )


def test_seal_open_round_trip_same_plaintext() -> None:
    ring = _ring("v1")
    plaintext = b"top-secret-api-key"
    rec = seal(plaintext, ring)
    assert rec.kid == "v1"
    assert open_(rec, ring) == plaintext


def test_seal_writes_active_kid() -> None:
    ring = _ring("v2")
    rec = seal(b"payload", ring)
    assert rec.kid == "v2"


def test_rewrap_changes_kid_and_preserves_plaintext() -> None:
    rec = seal(b"rotate-me", _ring("v1"))

    rotating_ring = _ring("v2")
    rewrapped = rewrap(rec, rotating_ring)

    assert rewrapped.kid == "v2"
    assert rewrapped.nonce == rec.nonce
    assert rewrapped.ciphertext == rec.ciphertext
    assert rewrapped.wrapped_dek != rec.wrapped_dek
    assert open_(rewrapped, rotating_ring) == b"rotate-me"


def test_rewrap_result_only_decrypts_with_new_kid() -> None:
    """rewrap 이후에는 새 kid만 가진 ring으로 복호되고, 옛 kid만 남은
    ring(새 kid가 없음)으로는 UnknownKeyIdError로 실패해야 한다."""
    rec = seal(b"only-new-kid", _ring("v1"))
    rewrapped = rewrap(rec, _ring("v2"))

    new_kid_only_ring = _single_kid_ring("v2", KEY_V2)
    assert open_(rewrapped, new_kid_only_ring) == b"only-new-kid"

    old_kid_only_ring = _single_kid_ring("v1", KEY_V1)
    with pytest.raises(UnknownKeyIdError):
        open_(rewrapped, old_kid_only_ring)


def test_open_unknown_kid_raises() -> None:
    ring = _ring("v1")
    rec = seal(b"x", ring)
    tampered = rec.model_copy(update={"kid": "v99"})
    with pytest.raises(UnknownKeyIdError):
        open_(tampered, ring)


def test_open_rejects_kid_swap_because_wrap_aad_binds_kid() -> None:
    """wrapped_dek는 그대로 두고 kid 필드만 다른 유효 kid로 바꿔치기하면
    키·AAD가 함께 달라져 인증 실패(InvalidTag)한다."""
    ring = _ring("v1")
    rec = seal(b"bound", ring)
    swapped = rec.model_copy(update={"kid": "v2"})
    with pytest.raises(InvalidTag):
        open_(swapped, ring)


def test_open_rejects_tampered_ciphertext() -> None:
    ring = _ring("v1")
    rec = seal(b"tamper-check", ring)
    tampered_ct = bytearray(rec.ciphertext)
    tampered_ct[0] ^= 0xFF
    tampered = rec.model_copy(update={"ciphertext": bytes(tampered_ct)})
    with pytest.raises(InvalidTag):
        open_(tampered, ring)


def test_open_rejects_tampered_wrapped_dek() -> None:
    ring = _ring("v1")
    rec = seal(b"tamper-wrap", ring)
    tampered_wrap = bytearray(rec.wrapped_dek)
    tampered_wrap[-1] ^= 0xFF
    tampered = rec.model_copy(update={"wrapped_dek": bytes(tampered_wrap)})
    with pytest.raises(InvalidTag):
        open_(tampered, ring)
