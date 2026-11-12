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
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

KEY_V1 = "11" * 32
KEY_V2 = "22" * 32


def _ring(active_kid: str = "v1") -> KeyRing:
    return KeyRing({"v1": bytes.fromhex(KEY_V1), "v2": bytes.fromhex(KEY_V2)}, active_kid)


# --- interface conformance ---------------------------------------------

# Measured on Windows 11 Pro (host: claude-local-1 worktree, 2026-11-11):
#   isinstance(adapter, KmsPort) ≈ 0.0025 ms/call (batch=100_000, process_time)
# Budget = 30× measured value = 0.075 ms; rounded up to 0.1 ms (40×).
# See perf_budget.py §batch: batch large enough to overcome 15.6 ms Windows tick.
_CONFORMANCE_BUDGET_MS: float = 0.1  # 30× floor; real ≈ 0.0025 ms (host: wt/claude-local-1)


def test_local_adapter_satisfies_kms_port_protocol_statically() -> None:
    """mypy structural check: this assignment only type-checks if
    `LocalKeyRingKmsAdapter` implements every `KmsPort` member with a
    compatible signature."""
    adapter: KmsPort = LocalKeyRingKmsAdapter(_ring())
    assert isinstance(adapter, KmsPort)


@pytest.mark.parametrize("missing", ["get_key", "encrypt", "decrypt", "rotate"])
def test_static_conformance_breaks_when_any_single_member_is_not_callable(
    missing: str,
) -> None:
    """Negative: nulling any one KmsPort member on a subclass fails isinstance."""
    broken_cls = type("_Broken", (LocalKeyRingKmsAdapter,), {missing: None})
    assert isinstance(broken_cls(_ring()), KmsPort) is False


# Measured 2026-11-12 (wt/backend-1, Windows 11): construct adapter + isinstance
# ≈ 0.0002 ms/call (20_000 iterations, perf_counter). Budget 0.05 ms (>200× measured).
_STATIC_CONFORMANCE_BUDGET_MS: float = 0.05


def _construct_and_check() -> bool:
    return isinstance(LocalKeyRingKmsAdapter(_ring()), KmsPort)


@pytest.mark.perf
def test_static_conformance_construct_and_check_within_perf_budget(
    perf_budget: PerfBudget,
) -> None:
    """Perf: adapter construction plus the protocol check stays under budget."""
    sample = perf_budget.best_of(_construct_and_check, n=10, batch=500)
    assert sample.cpu_ms < _STATIC_CONFORMANCE_BUDGET_MS, (
        f"cpu={sample.cpu_ms:.4f}ms budget={_STATIC_CONFORMANCE_BUDGET_MS}ms"
    )


@pytest.mark.perf
def test_static_conformance_gate_fails_on_10x_virtual_delay(
    perf_budget: PerfBudget,
) -> None:
    """Gate-red: a 10× virtual delay must trip the static-conformance budget."""
    reset_virtual_totals()
    delayed = virtual_delayed_sync(
        _construct_and_check, 10 * _STATIC_CONFORMANCE_BUDGET_MS / 1000.0
    )
    sample = perf_budget.best_of(delayed, n=5, batch=1)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < _STATIC_CONFORMANCE_BUDGET_MS


def test_object_missing_kms_port_member_is_not_kms_port() -> None:
    """Negative: an object missing any KmsPort member must not pass
    ``isinstance(..., KmsPort)``."""

    class PartialAdapter:
        def get_key(self, kid: str) -> bytes:
            return b""

        def encrypt(self, plaintext: str) -> str:
            return ""

        def decrypt(self, token: str) -> str:
            return ""

        # Missing: rotate

    adapter = PartialAdapter()
    assert isinstance(adapter, KmsPort) is False


def test_plain_object_and_none_are_not_kms_port() -> None:
    """Negative: bare ``object()`` and ``None`` are not ``KmsPort``."""
    assert isinstance(object(), KmsPort) is False
    assert isinstance(None, KmsPort) is False


# --- round trip parity (behaviour must not change through the port) ----


@pytest.mark.perf
@pytest.mark.parametrize("inject_delay", [False, True], ids=["budget", "gate-red-10x"])
def test_encrypt_decrypt_roundtrip_via_port_matches_direct_encryption_call(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
    inject_delay: bool,
) -> None:
    """Reuse the existing 5ms budget; verify parity and reject 10x virtual delay."""
    ring = _ring()
    adapter = LocalKeyRingKmsAdapter(ring)

    token = adapter.encrypt("hello FA-22")
    assert adapter.decrypt(token) == "hello FA-22"

    # Cross-check against calling encryption.py directly with the same ring —
    # the port must not change the wire format or the round-trip result.
    direct_token = encryption.encrypt("hello FA-22", ring)
    assert encryption.decrypt(token, ring) == "hello FA-22"
    assert encryption.decrypt(direct_token, ring) == adapter.decrypt(direct_token)

    def roundtrip() -> str:
        encrypted = adapter.encrypt("hello FA-22")
        assert encryption.decrypt(encrypted, ring) == "hello FA-22"
        return adapter.decrypt(encrypted)

    def check_budget(cpu_ms: float) -> None:
        assert cpu_ms < _ROUNDTRIP_BUDGET_MS, "FA-22 roundtrip budget exceeded"

    reset_virtual_totals()
    try:
        if inject_delay:
            monkeypatch.setattr(
                encryption,
                "encrypt",
                virtual_delayed_sync(encryption.encrypt, 10 * _ROUNDTRIP_BUDGET_MS / 1000),
            )
        sample = perf_budget.best_of(roundtrip, n=5, batch=256)
        assert sample.result == "hello FA-22"
        if inject_delay:
            assert sample.cpu_ms >= 10 * _ROUNDTRIP_BUDGET_MS
            with pytest.raises(AssertionError, match="FA-22 roundtrip budget exceeded"):
                check_budget(sample.cpu_ms)
        else:
            check_budget(sample.cpu_ms)
    finally:
        reset_virtual_totals()


# --- negatives for roundtrip -------------------------------------------


def test_encrypt_decrypt_roundtrip_via_port_empty_plaintext() -> None:
    """Negative: encrypting an empty string must still round-trip cleanly."""
    adapter = LocalKeyRingKmsAdapter(_ring())
    token = adapter.encrypt("")
    assert adapter.decrypt(token) == ""


def test_encrypt_decrypt_roundtrip_via_port_unicode_payload() -> None:
    """Negative: non-ASCII / emoji payloads must survive the round trip."""
    adapter = LocalKeyRingKmsAdapter(_ring())
    payload = "한글 🇰🇷 emoji ☁️ mixed"
    token = adapter.encrypt(payload)
    assert adapter.decrypt(token) == payload


def test_encrypt_decrypt_roundtrip_via_port_max_size_payload() -> None:
    """Negative: payload at the AES-GCM block boundary (256 bytes) must
    round-trip without truncation."""
    adapter = LocalKeyRingKmsAdapter(_ring())
    payload = "A" * 256
    token = adapter.encrypt(payload)
    assert adapter.decrypt(token) == payload


# --- failure injection for roundtrip -----------------------------------


def test_encrypt_decrypt_roundtrip_via_port_failure_injection_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure injection: if ``encryption.encrypt`` raises during the
    round trip the adapter must propagate the original exception."""

    def _boom(plaintext: str, ring: KeyRing) -> str:
        raise RuntimeError("KMS backend unreachable")

    monkeypatch.setattr(encryption, "encrypt", _boom)
    adapter = LocalKeyRingKmsAdapter(_ring())
    with pytest.raises(RuntimeError, match="KMS backend unreachable"):
        adapter.encrypt("should-fail")


# --- gate-red + perf for roundtrip -------------------------------------

_ROUNDTRIP_PERF_BUDGET_MS: float = 5.0  # 30× real ≈ 0.15 ms (measured on host 2026-11-11)


@pytest.mark.perf
def test_encrypt_decrypt_roundtrip_via_port_within_perf_budget(
    perf_budget: PerfBudget,
) -> None:
    """Performance gate: encrypt+decrypt round trip through the port must
    complete well under ``_ROUNDTRIP_PERF_BUDGET_MS`` (5 ms; 30× measured
    ≈ 0.15 ms on host wt/claude-local-1, 2026-11-11)."""
    adapter = LocalKeyRingKmsAdapter(_ring())

    def _roundtrip() -> str:
        token = adapter.encrypt("perf-budget-check")
        return adapter.decrypt(token)

    sample = perf_budget.best_of(_roundtrip, n=5)
    assert sample.cpu_ms < _ROUNDTRIP_PERF_BUDGET_MS, (
        f"[FA-22] round trip cpu={sample.cpu_ms:.4f}ms "
        f"wall={sample.wall_ms:.4f}ms budget<{_ROUNDTRIP_PERF_BUDGET_MS:.0f}ms"
    )


@pytest.mark.perf
def test_encrypt_decrypt_roundtrip_via_port_gate_red_fails_with_10x_virtual_delay(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """Gate-red reproduction: inject a 10× virtual delay (50 ms) through
    ``virtual_delayed_sync`` and confirm the budget assertion catches it.
    Uses ``reset_virtual_totals`` for idempotency."""
    reset_virtual_totals()
    original_encrypt = encryption.encrypt
    delay_s = 10 * _ROUNDTRIP_PERF_BUDGET_MS / 1000.0  # 50 ms = 10× budget

    monkeypatch.setattr(encryption, "encrypt", virtual_delayed_sync(original_encrypt, delay_s))
    adapter = LocalKeyRingKmsAdapter(_ring())

    def _roundtrip() -> str:
        token = adapter.encrypt("gate-red")
        return adapter.decrypt(token)

    sample = perf_budget.best_of(_roundtrip, n=5)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < _ROUNDTRIP_PERF_BUDGET_MS


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


@pytest.mark.perf
def test_roundtrip_p95_latency_within_self_declared_budget() -> None:
    adapter = LocalKeyRingKmsAdapter(_ring())
    samples = _roundtrip_latencies_ms(adapter, _ROUNDTRIP_ITERATIONS)
    p95_ms = _p95(samples)
    print(
        f"[FA-22] LocalKeyRingKmsAdapter round trip p95={p95_ms:.4f}ms "
        f"budget<{_ROUNDTRIP_BUDGET_MS:.0f}ms"
    )
    assert p95_ms < _ROUNDTRIP_BUDGET_MS


def test_roundtrip_p95_latency_within_self_declared_budget_perf_budget(
    perf_budget: PerfBudget,
) -> None:
    """Perf-budget measurement: one encrypt+decrypt round trip measured via
    ``PerfBudget.best_of``.  Realised cpu_ms ≈ 0.17 ms on this host; budget
    is 5 ms (≈ 30× 여유).  Reuses the existing ``_ROUNDTRIP_BUDGET_MS`` — no
    new budget introduced."""
    adapter = LocalKeyRingKmsAdapter(_ring())

    def _roundtrip() -> str:
        token = adapter.encrypt("payload")
        return adapter.decrypt(token)

    sample = perf_budget.best_of(_roundtrip, n=5)
    # fmt: off
    assert sample.cpu_ms < _ROUNDTRIP_BUDGET_MS, (
        f"[FA-22] round trip cpu={sample.cpu_ms:.4f}ms "
        f"wall={sample.wall_ms:.4f}ms budget<{_ROUNDTRIP_BUDGET_MS:.0f}ms"
    )
    # fmt: on


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


@pytest.mark.perf
def test_roundtrip_budget_gate_actually_fails_past_budget_virtual_delay(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """Gate-red reproduction via virtual delay (no actual sleep): injects a
    10× delay (50 ms) through ``virtual_delayed_sync`` and confirms the
    assertion catches it as a red signal.  Uses ``reset_virtual_totals`` at
    start to keep the test idempotent."""
    reset_virtual_totals()
    original_encrypt = encryption.encrypt
    delay_s = 10 * _ROUNDTRIP_BUDGET_MS / 1000.0  # 50 ms = 10× budget

    monkeypatch.setattr(encryption, "encrypt", virtual_delayed_sync(original_encrypt, delay_s))
    adapter = LocalKeyRingKmsAdapter(_ring())

    def _roundtrip() -> str:
        token = adapter.encrypt("payload")
        return adapter.decrypt(token)

    sample = perf_budget.best_of(_roundtrip, n=5)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < _ROUNDTRIP_BUDGET_MS


# --- protocol conformance failure injection & perf gates ----------------


def test_adapter_missing_member_after_injected_removal_fails_conformance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure injection: override ``encrypt`` on a subclass with ``None``
    and confirm ``isinstance`` reports ``False``.

    ``monkeypatch.delattr`` on the class doesn't work here — Python 3.10's
    ``@runtime_checkable`` Protocol caches the ABC conformity check, and
    deleting a method from the class ``__dict__`` doesn't invalidate the
    cache.  Overriding with a non-callable (``None``) is the reliable way
    to break conformance at runtime."""
    adapter = LocalKeyRingKmsAdapter(_ring())
    assert isinstance(adapter, KmsPort) is True

    class _BrokenAdapter(LocalKeyRingKmsAdapter):
        pass

    _BrokenAdapter.encrypt = None
    broken = _BrokenAdapter(_ring())
    assert isinstance(broken, KmsPort) is False


@pytest.mark.perf
def test_protocol_conformance_check_within_perf_budget(
    perf_budget: PerfBudget,
) -> None:
    """Performance gate: ``isinstance(adapter, KmsPort)`` must complete
    well under ``_CONFORMANCE_BUDGET_MS`` (0.1 ms; 40× measured 0.0025 ms).
    Uses ``batch`` to overcome Windows ``process_time`` 15.6 ms quantization."""
    adapter = LocalKeyRingKmsAdapter(_ring())
    sample = perf_budget.best_of(lambda: isinstance(adapter, KmsPort), n=10, batch=500)
    assert sample.cpu_ms < _CONFORMANCE_BUDGET_MS, (
        f"conformance check too slow: cpu={sample.cpu_ms:.3f}ms "
        f"wall={sample.wall_ms:.3f}ms budget={_CONFORMANCE_BUDGET_MS}ms"
    )


@pytest.mark.perf
def test_protocol_conformance_gate_fails_on_10x_virtual_delay(
    perf_budget: PerfBudget,
) -> None:
    """Gate-red reproduction: wrap the check itself in a 10× virtual delay
    and confirm the budget assertion catches it as a red signal.
    Uses ``reset_virtual_totals`` at start for idempotency.

    Note: we wrap the callable directly (not monkeypatch ``encryption.encrypt``)
    because the ``isinstance`` path never calls encrypt — only a direct wrapper
    injection works."""
    reset_virtual_totals()
    adapter = LocalKeyRingKmsAdapter(_ring())
    delay_s = 10 * _CONFORMANCE_BUDGET_MS / 1000.0  # 10× budget in seconds

    delayed_check = virtual_delayed_sync(lambda: isinstance(adapter, KmsPort), delay_s)
    sample = perf_budget.best_of(delayed_check, n=5, batch=1)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < _CONFORMANCE_BUDGET_MS
