"""`KmsStub` (FA-23) unit tests.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-23
(task-5916, re-issue of task-2680). FA axis floor is D3 (ADR-2026-09-09-C):
D2 (negative>=3, failure-injection 1, perf assertion 1, gate-red repro 1)
plus an adversarial cross-check against `docs/design/INVARIANTS.md`. `KmsStub`
is a pure, I/O-free in-memory crypto utility, so there is no event log to
replay; the D3 slot is filled instead with an adversarial multi-instance
proof (I-07: a fail-closed hard-fail path must actually FAIL; I-10:
"implemented != wired" — prove key material never leaks across instances).
"""
from __future__ import annotations

import time

import pytest
from cryptography.exceptions import InvalidTag

from src.core.security import encryption
from src.core.security.key_ring import KeyRing
from src.core.security.kms_port import KmsPort
from src.core.security.kms_stub import KeyNotFoundError, KmsStub, KmsStubConfigError

# --- interface conformance ---------------------------------------------


def test_stub_satisfies_kms_port_protocol_statically() -> None:
    """mypy structural check: this assignment only type-checks if `KmsStub`
    implements every `KmsPort` member with a compatible signature."""
    stub: KmsPort = KmsStub()
    assert isinstance(stub, KmsPort)


# --- happy path -----------------------------------------------------------


def test_encrypt_decrypt_roundtrip() -> None:
    stub = KmsStub()
    token = stub.encrypt("hello FA-23")
    assert stub.decrypt(token) == "hello FA-23"


def test_get_key_returns_minted_key_bytes() -> None:
    stub = KmsStub(initial_key=b"\x01" * 32)
    assert stub.get_key("v1") == b"\x01" * 32


# --- core DoD: rotate() increases version, old ciphertext stays readable --


def test_rotate_mints_new_key_and_preserves_old_ciphertext_decryption() -> None:
    stub = KmsStub(initial_key=b"\x01" * 32)
    token_v1 = stub.encrypt("payload")

    rotated = stub.rotate("v2")

    assert rotated is not stub
    assert rotated.active_kid == "v2"
    # New key material, distinct from v1's.
    assert rotated.get_key("v2") != stub.get_key("v1")
    # Backward compatibility: v1-encrypted ciphertext still decrypts.
    assert rotated.decrypt(token_v1) == "payload"
    # New encryptions use the rotated (v2) active kid.
    token_v2 = rotated.encrypt("payload")
    assert token_v2.split("$")[1] == "v2"
    assert rotated.decrypt(token_v2) == "payload"


def test_rotate_leaves_original_instance_unchanged() -> None:
    stub = KmsStub(initial_key=b"\x01" * 32)
    token_v1 = stub.encrypt("payload")

    stub.rotate("v2")

    assert stub.active_kid == "v1"
    assert stub.decrypt(token_v1) == "payload"


def test_chained_rotations_keep_every_prior_kid_decryptable() -> None:
    stub = KmsStub(initial_key=b"\x01" * 32)
    token_v1 = stub.encrypt("a")
    stub_v2 = stub.rotate("v2")
    token_v2 = stub_v2.encrypt("b")
    stub_v3 = stub_v2.rotate("v3")

    assert stub_v3.decrypt(token_v1) == "a"
    assert stub_v3.decrypt(token_v2) == "b"


# --- negative tests (>=3) ------------------------------------------------


def test_get_key_unknown_kid_raises_key_not_found_error() -> None:
    stub = KmsStub()
    with pytest.raises(KeyNotFoundError):
        stub.get_key("does-not-exist")


def test_decrypt_token_with_kid_absent_from_this_stub_raises_key_not_found_error() -> None:
    """DoD negative test: an unknown `key_id` presented to `decrypt` must
    raise `KeyNotFoundError`, not a bare `KeyError`/`UnknownKeyIdError` from
    the underlying `KeyRing` (the `KmsPort` boundary must translate it)."""
    minted_elsewhere = KmsStub(initial_kid="v-other", initial_key=b"\x02" * 32)
    foreign_token = minted_elsewhere.encrypt("secret")

    stub = KmsStub(initial_key=b"\x01" * 32)  # has no "v-other" kid at all
    with pytest.raises(KeyNotFoundError):
        stub.decrypt(foreign_token)


def test_rotate_to_already_existing_kid_raises_config_error() -> None:
    stub = KmsStub()
    with pytest.raises(KmsStubConfigError):
        stub.rotate("v1")  # v1 already exists — rotate must not overwrite it


def test_decrypt_tampered_token_raises() -> None:
    stub = KmsStub()
    token = stub.encrypt("secret")
    prefix, kid, body = token.split("$", 2)
    tampered = f"{prefix}${kid}${body[:-4]}AAAA"
    with pytest.raises(InvalidTag):
        stub.decrypt(tampered)


# --- failure injection ----------------------------------------------------


def test_encrypt_propagates_underlying_encryption_failure_instead_of_swallowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fail-closed: if `encryption.encrypt` raises (e.g. a future real KMS
    backend outage), the stub must propagate the original exception, not
    report a misleadingly successful token or swallow it into a generic
    error."""

    def _boom(plaintext: str, ring: KeyRing) -> str:
        raise RuntimeError("simulated KMS backend failure")

    monkeypatch.setattr(encryption, "encrypt", _boom)
    stub = KmsStub()
    with pytest.raises(RuntimeError, match="simulated KMS backend failure"):
        stub.encrypt("secret")


# --- D3: adversarial / multi-instance proof --------------------------------


def test_decrypt_with_completely_foreign_instance_key_material_fails_closed() -> None:
    """Adversarial (I-07, I-10): two independently constructed `KmsStub`
    instances (distinct kid namespaces, as a real KMS's per-tenant/per-env
    key material would be) never share key material by accident. A token
    minted by one must never silently decrypt (or wrongly succeed) against
    the other — it must hard-fail with `KeyNotFoundError`, proving the
    fail-closed path is actually wired, not just declared."""
    attacker_stub = KmsStub(initial_key=b"\xaa" * 32)
    victim_stub = KmsStub(initial_kid="v-victim", initial_key=b"\xbb" * 32)

    victim_token = victim_stub.encrypt("victim-secret")
    with pytest.raises(KeyNotFoundError):
        attacker_stub.decrypt(victim_token)


def test_independent_rotation_chains_never_cross_contaminate_key_material() -> None:
    """Multi-instance proof: rotating two independently-seeded stubs down
    parallel chains never lets one chain's tokens decrypt under the other's
    key material, even when both chains reuse the same kid labels
    ("v2", "v3", ...)."""
    base_a = KmsStub(initial_key=b"\x01" * 32)
    base_b = KmsStub(initial_key=b"\x02" * 32)

    chain_a = base_a.rotate("v2").rotate("v3")
    chain_b = base_b.rotate("v2").rotate("v3")

    token_a = chain_a.encrypt("secret-a")
    token_b = chain_b.encrypt("secret-b")

    assert chain_a.decrypt(token_a) == "secret-a"
    assert chain_b.decrypt(token_b) == "secret-b"
    # Same kid label ("v3") on both chains, but different key bytes underneath —
    # cross-decryption must fail closed rather than silently returning garbage.
    with pytest.raises(InvalidTag):
        chain_a.decrypt(token_b)
    with pytest.raises(InvalidTag):
        chain_b.decrypt(token_a)


# --- numeric performance assertion ----------------------------------------

_ROUNDTRIP_ITERATIONS = 50
_ROUNDTRIP_BUDGET_MS = 5.0  # ADR-2026-09-09-C Decision 1 has no dedicated KMS-stub
# budget row; borrows the nearest analogue ("pre-trade gate p99 5ms") like the
# FA-22 LocalKeyRingKmsAdapter perf test — AES-GCM round trip on a 32-byte key
# with no I/O is expected to be orders of magnitude under this.


def _roundtrip_latencies_ms(stub: KmsStub, iterations: int) -> list[float]:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        token = stub.encrypt("payload")
        stub.decrypt(token)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


def test_roundtrip_p95_latency_within_self_declared_budget() -> None:
    stub = KmsStub()
    samples = _roundtrip_latencies_ms(stub, _ROUNDTRIP_ITERATIONS)
    p95_ms = _p95(samples)
    print(f"[FA-23] KmsStub round trip p95={p95_ms:.4f}ms budget<{_ROUNDTRIP_BUDGET_MS:.0f}ms")
    assert p95_ms < _ROUNDTRIP_BUDGET_MS


def test_roundtrip_budget_gate_actually_fails_past_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Red-gate reproduction: prove the assertion above is not a tautology
    by injecting latency and confirming it actually fails."""
    original_encrypt = encryption.encrypt

    def _slow_encrypt(plaintext: str, ring: KeyRing) -> str:
        time.sleep(_ROUNDTRIP_BUDGET_MS / 1000.0)
        return original_encrypt(plaintext, ring)

    monkeypatch.setattr(encryption, "encrypt", _slow_encrypt)
    stub = KmsStub()
    samples = _roundtrip_latencies_ms(stub, iterations=3)
    p95_ms = _p95(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _ROUNDTRIP_BUDGET_MS
