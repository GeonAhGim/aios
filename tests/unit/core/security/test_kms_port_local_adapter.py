"""`LocalKeyRingKmsAdapter` (KmsPort) unit tests.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-22
(task-5915, re-issue of task-2679). Core assertion: routing the existing
`KeyRing` encrypt/decrypt round trip through the `KmsPort` adapter produces
byte-identical results to calling `src.core.security.encryption` directly —
the port is a pure indirection, not a behaviour change.
"""
from __future__ import annotations

import time

import pytest
from cryptography.exceptions import InvalidTag

from src.core.security import encryption
from src.core.security.encryption import LocalKeyRingKmsAdapter
from src.core.security.key_ring import KeyRing, KeyRingConfigError, UnknownKeyIdError
from src.core.security.kms_port import KmsPort

KEY_V1 = "11" * 32
KEY_V2 = "22" * 32


def _ring(active_kid: str = "v1") -> KeyRing:
    return KeyRing({"v1": bytes.fromhex(KEY_V1), "v2": bytes.fromhex(KEY_V2)}, active_kid)


# --- interface conformance ---------------------------------------------


def test_local_adapter_satisfies_kms_port_protocol_statically() -> None:
    """mypy structural check: this assignment only type-checks if
    `LocalKeyRingKmsAdapter` implements every `KmsPort` member with a
    compatible signature."""
    adapter: KmsPort = LocalKeyRingKmsAdapter(_ring())
    assert isinstance(adapter, KmsPort)


# --- round trip parity (behaviour must not change through the port) ----


def test_encrypt_decrypt_roundtrip_via_port_matches_direct_encryption_call() -> None:
    ring = _ring()
    adapter = LocalKeyRingKmsAdapter(ring)

    token = adapter.encrypt("hello FA-22")
    assert adapter.decrypt(token) == "hello FA-22"

    # Cross-check against calling encryption.py directly with the same ring —
    # the port must not change the wire format or the round-trip result.
    direct_token = encryption.encrypt("hello FA-22", ring)
    assert encryption.decrypt(token, ring) == "hello FA-22"
    assert encryption.decrypt(direct_token, ring) == adapter.decrypt(direct_token)


def test_get_key_returns_same_bytes_as_key_ring() -> None:
    ring = _ring()
    adapter = LocalKeyRingKmsAdapter(ring)
    assert adapter.get_key("v1") == ring.key("v1")


def test_rotate_returns_new_port_and_leaves_original_unchanged() -> None:
    ring = _ring(active_kid="v1")
    adapter = LocalKeyRingKmsAdapter(ring)

    token_before = adapter.encrypt("payload")
    rotated = adapter.rotate("v2")

    assert rotated is not adapter
    assert rotated.get_key("v2") == ring.key("v2")
    # Old adapter is untouched (KeyRing immutability preserved through the port).
    assert adapter.decrypt(token_before) == "payload"
    # New adapter can still decrypt ciphertext wrapped under the old kid
    # (both kids remain in the shared key material) ...
    assert rotated.decrypt(token_before) == "payload"
    # ... but newly encrypted payloads use the rotated active kid.
    token_after = rotated.encrypt("payload")
    assert token_after.split("$")[1] == "v2"


# --- negative tests (>=3) ------------------------------------------------


def test_get_key_unknown_kid_raises() -> None:
    adapter = LocalKeyRingKmsAdapter(_ring())
    with pytest.raises(UnknownKeyIdError):
        adapter.get_key("does-not-exist")


def test_decrypt_tampered_token_raises() -> None:
    adapter = LocalKeyRingKmsAdapter(_ring())
    token = adapter.encrypt("secret")
    prefix, kid, body = token.split("$", 2)
    tampered = f"{prefix}${kid}${body[:-4]}AAAA"
    with pytest.raises(InvalidTag):
        adapter.decrypt(tampered)


def test_rotate_to_unknown_kid_raises() -> None:
    adapter = LocalKeyRingKmsAdapter(_ring())
    with pytest.raises(KeyRingConfigError):
        adapter.rotate("v99")


# --- failure injection ----------------------------------------------------


def test_encrypt_propagates_underlying_encryption_failure_instead_of_swallowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fail-closed: if `encryption.encrypt` raises (e.g. a future KMS backend
    outage), the adapter must propagate the original exception, not report
    a misleadingly successful token or swallow it into a generic error."""

    def _boom(plaintext: str, ring: KeyRing) -> str:
        raise RuntimeError("simulated KMS backend failure")

    monkeypatch.setattr(encryption, "encrypt", _boom)
    adapter = LocalKeyRingKmsAdapter(_ring())
    with pytest.raises(RuntimeError, match="simulated KMS backend failure"):
        adapter.encrypt("secret")


# --- numeric performance assertion ----------------------------------------

_ROUNDTRIP_ITERATIONS = 50
_ROUNDTRIP_BUDGET_MS = 5.0  # ADR-2026-09-09-C Decision 1 has no dedicated KMS-port
# budget row; borrows the nearest analogue ("pre-trade gate p99 5ms") like the
# existing KeyRing.from_env perf test — AES-GCM round trip on a 32-byte key with
# no I/O is expected to be orders of magnitude under this.


def _roundtrip_latencies_ms(adapter: LocalKeyRingKmsAdapter, iterations: int) -> list[float]:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        token = adapter.encrypt("payload")
        adapter.decrypt(token)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


def test_roundtrip_p95_latency_within_self_declared_budget() -> None:
    adapter = LocalKeyRingKmsAdapter(_ring())
    samples = _roundtrip_latencies_ms(adapter, _ROUNDTRIP_ITERATIONS)
    p95_ms = _p95(samples)
    print(
        f"[FA-22] LocalKeyRingKmsAdapter round trip p95={p95_ms:.4f}ms "
        f"budget<{_ROUNDTRIP_BUDGET_MS:.0f}ms"
    )
    assert p95_ms < _ROUNDTRIP_BUDGET_MS


def test_roundtrip_budget_gate_actually_fails_past_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Red-gate reproduction: prove the assertion above is not a tautology by
    injecting latency and confirming it actually fails."""
    original_encrypt = encryption.encrypt

    def _slow_encrypt(plaintext: str, ring: KeyRing) -> str:
        time.sleep(_ROUNDTRIP_BUDGET_MS / 1000.0)
        return original_encrypt(plaintext, ring)

    monkeypatch.setattr(encryption, "encrypt", _slow_encrypt)
    adapter = LocalKeyRingKmsAdapter(_ring())
    samples = _roundtrip_latencies_ms(adapter, iterations=3)
    p95_ms = _p95(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _ROUNDTRIP_BUDGET_MS
