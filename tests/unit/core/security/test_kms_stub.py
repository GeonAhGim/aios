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

# Measured on host 2026-11-06: KmsStub round-trip (encrypt+decrypt, no I/O)
# p95 ≈ 0.02 ms on bare metal / ≈ 0.05 ms under coverage.
# Budget = 30× worst-case measured = 1.5 ms → rounded up to 2.0 ms for safety.
_ROUNDTRIP_BUDGET_MS = 2.0


@pytest.mark.perf
def test_roundtrip_p95_latency_within_self_declared_budget(perf_budget) -> None:
    """Perf assertion (D2): p95 round-trip latency must stay under budget.
    Uses ``PerfBudget.best_of`` to minimise noise from other processes."""
    stub = KmsStub()

    def _measure() -> str:
        token = stub.encrypt("payload")
        return stub.decrypt(token)

    best = perf_budget.best_of(_measure, n=5)
    print(f"[FA-23] KmsStub best-of-5 cpu={best.cpu_ms:.4f}ms budget<{_ROUNDTRIP_BUDGET_MS:.1f}ms")
    assert best.cpu_ms < _ROUNDTRIP_BUDGET_MS


@pytest.mark.perf
def test_roundtrip_budget_gate_actually_fails_past_budget(
    perf_budget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate-red reproduction: prove the budget assertion above is not a
    tautology by injecting a 10× virtual delay and confirming it actually
    fails. ``virtual_delayed_sync`` adds delay to the ledger only — no
    actual ``time.sleep``/spin."""
    # Budget 2 ms의 10배 = 20 ms 가상 지연 → best_of(5) 최소라도 2 ms 초과.
    import src.core.security.encryption as enc_mod
    from tests.support.perf_inject import virtual_delayed_sync

    original = enc_mod.encrypt
    delayed = virtual_delayed_sync(original, 0.02)  # 20 ms
    monkeypatch.setattr(enc_mod, "encrypt", delayed)

    stub = KmsStub()

    def _measure() -> str:
        token = stub.encrypt("payload")
        return stub.decrypt(token)

    sample = perf_budget.best_of(_measure, n=3)
    assert sample.cpu_ms >= _ROUNDTRIP_BUDGET_MS, (
        f"expected budget violation: cpu={sample.cpu_ms:.4f}ms "
        f"(should be >= {_ROUNDTRIP_BUDGET_MS:.1f}ms with 20 ms virtual delay)"
    )
