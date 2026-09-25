"""encryption.py 포맷 단위 테스트 — `aios1$<kid>$<b64>` + 레거시 복호.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-31
(+ §2 101행). 핵심 불변: 기존 `legacy_encrypt`가 만든 토큰이 새
`decrypt(token, ring)`으로 그대로(평문 왕복) 복호돼야 한다.

DEEPEN(task-3138): task-458 DEPTH 감사가 원 리프(commit 401c16dd)에 실패
주입·수치 성능 단언·게이트 적색 재현이 없다고 지적함 — 새 기능 추가 없이
이 리프의 증빙만 보강한다. 실패 주입은 근본 암호 라이브러리(AESGCM)가
`InvalidTag`가 아닌 예기치 못한 예외를 던졌을 때 `decrypt`가 이를 삼켜
"복호 실패=평문 없음"을 성공처럼 위장하지 않고 그대로 전파하는지 확인한다
(fail-closed 회귀 방지). 성능 단언은 ADR-2026-09-09-C Decision 1 예산표에
`encrypt`/`decrypt` 전용 항목이 없어(가장 가까운 항목은 "사전거래 게이트
p99 5ms") 이 리프가 그 값을 자체 예산으로 차용한다 — AES-256-GCM 왕복은
순수 CPU 연산(디스크·네트워크 없음)이라 로컬 실측 p95는 이보다 수백 배
낮다(2026-09-16 실측 ~0.01ms).
"""

from __future__ import annotations

import time

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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


# --- DEEPEN(task-3138): 실패 주입 — AESGCM 내부 실패가 삼켜지지 않는지 -----


def test_decrypt_propagates_unexpected_aesgcm_failure_instead_of_swallowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: 근본 암호 라이브러리(AESGCM.decrypt)가 `InvalidTag`가 아닌
    예기치 못한 예외(예: 손상된 크립토 백엔드)를 던지면 `decrypt()`가 이를
    삼켜 '복호 실패=평문 없음'을 성공처럼 위장하지 않고 그대로 전파해야
    한다(fail-closed) — 향후 누군가 여기 broad except를 추가해도 이
    테스트가 잡는다."""
    ring = _ring()
    token = encrypt("payload", ring)

    def _boom(self: AESGCM, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        raise RuntimeError("simulated crypto backend failure")

    monkeypatch.setattr(AESGCM, "decrypt", _boom)
    with pytest.raises(RuntimeError, match="simulated crypto backend failure"):
        decrypt(token, ring)


# --- DEEPEN(task-3138): 수치 성능 단언 — encrypt+decrypt 왕복 지연 ---------

_ROUND_TRIP_ITERATIONS = 50
_ROUND_TRIP_BUDGET_MS = 5.0  # ADR-2026-09-09-C Decision 1의 "사전거래 게이트
# p99 5ms"를 가장 가까운 유사 항목으로 차용(전용 예산 항목 없음) — AES-256-GCM
# 왕복은 순수 CPU 연산(디스크·네트워크 없음)이라 로컬 실측 p95(2026-09-16
# ~0.01ms)는 이보다 수백 배 낮다.


def _round_trip_latencies_ms(iterations: int = _ROUND_TRIP_ITERATIONS) -> list[float]:
    ring = _ring()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        token = encrypt("perf-probe-plaintext", ring)
        decrypt(token, ring)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


def test_encrypt_decrypt_round_trip_p95_latency_within_self_declared_budget() -> None:
    samples = _round_trip_latencies_ms()
    p95_ms = _p95(samples)
    print(f"[PLT-31] encrypt+decrypt p95={p95_ms:.4f}ms budget<{_ROUND_TRIP_BUDGET_MS:.0f}ms")
    assert p95_ms < _ROUND_TRIP_BUDGET_MS


def test_round_trip_budget_gate_actually_fails_past_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: 위 단언식이 실제 지연 주입에 대해 `AssertionError`를
    내는지 확인한다 — 이 테스트가 없으면 위 단언이 항상 통과하는
    tautology인지 아무도 검증하지 못한다."""
    original_encrypt = AESGCM.encrypt

    def _slow_encrypt(
        self: AESGCM, nonce: bytes, data: bytes, associated_data: bytes | None
    ) -> bytes:
        time.sleep(_ROUND_TRIP_BUDGET_MS / 1000.0)
        return original_encrypt(self, nonce, data, associated_data)

    monkeypatch.setattr(AESGCM, "encrypt", _slow_encrypt)

    samples = _round_trip_latencies_ms(iterations=3)
    p95_ms = _p95(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _ROUND_TRIP_BUDGET_MS
