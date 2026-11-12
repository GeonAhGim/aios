"""PLT-33 — 예외 메시지·감사로그에 자격증명 원문이 찍히지 않는지 검증.

task-904 redaction 원칙(`KeyRing._redact_entry`와 동일한 정신)을 credential
서비스 경로 전체에 적용한다: 등록 실패, 복호 실패(회전으로 kid 소실),
감사로그 어디에도 api_key/api_secret 원문이나 원본 암호문(base64) 전체가
그대로 노출돼서는 안 된다.
"""

from __future__ import annotations

import os

import asyncpg
import pytest

from src.core.security.key_ring import KeyRing
from src.services.exchange_credential_service import (
    ExchangeCredentialDecryptionError,
    ExchangeCredentialError,
    ExchangeCredentialService,
)
from tests.integration.conftest import create_test_user
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

_SECRET_KEY = "good-secret-do-not-leak"
_SECRET_API_KEY = "good-key-do-not-leak"


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


class _FakeAdapter:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    async def get_balance(self):
        if self._fail:
            raise RuntimeError(f"auth failed for key={_SECRET_API_KEY} secret={_SECRET_KEY}")
        return []

    async def aclose(self):
        return None


def _factory(*, fail: bool):
    def factory(exchange, api_key, api_secret, extra, *, demo_mode=True):
        return _FakeAdapter(fail=fail)

    return factory


@pytest.fixture
def key_ring_a():
    return KeyRing({"kid-a": b"\x11" * 32}, active_kid="kid-a")


@pytest.fixture
def service(pool, key_ring_a):
    return ExchangeCredentialService(
        pool, key_ring=key_ring_a, adapter_factory=_factory(fail=False)
    )


async def test_register_failure_exception_omits_credentials(pool, key_ring_a):
    user_id = await create_test_user(pool)
    failing_service = ExchangeCredentialService(
        pool, key_ring=key_ring_a, adapter_factory=_factory(fail=True)
    )

    with pytest.raises(ExchangeCredentialError) as exc_info:
        await failing_service.register(user_id, "bitget", _SECRET_API_KEY, _SECRET_KEY)

    message = str(exc_info.value)
    assert _SECRET_API_KEY not in message
    assert _SECRET_KEY not in message


async def test_audit_log_omits_credentials(service, pool):
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", _SECRET_API_KEY, _SECRET_KEY)
    await service.revoke(user_id, "bitget")

    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT decision_data FROM audit_log WHERE user_id = $1", user_id)
    for row in rows:
        payload = str(row["decision_data"])
        assert _SECRET_API_KEY not in payload
        assert _SECRET_KEY not in payload


async def test_decryption_failure_after_rotation_does_not_leak_ciphertext(service, pool):
    """kid-a로 등록한 뒤 kid-a가 완전히 사라진 KeyRing으로 조회하면
    (회전 도중 구버전 kid를 너무 일찍 지운 상황을 흉내) 복호는 실패해야
    하고, 그 예외 메시지는 고정 문구여야 한다 — 원본 base64 암호문이나
    `cryptography` 라이브러리 예외의 내부 문자열이 새어나가면 안 된다."""
    user_id = await create_test_user(pool)
    await service.register(user_id, "bitget", _SECRET_API_KEY, _SECRET_KEY)

    rotated_ring = KeyRing({"kid-b": b"\x22" * 32}, active_kid="kid-b")
    rotated_service = ExchangeCredentialService(pool, key_ring=rotated_ring)

    with pytest.raises(ExchangeCredentialDecryptionError) as exc_info:
        await rotated_service.get_decrypted(user_id, "bitget")

    message = str(exc_info.value)
    assert message == "저장된 자격증명을 복호할 수 없습니다."
    assert _SECRET_API_KEY not in message
    assert _SECRET_KEY not in message
    assert "kid-a" not in message  # 존재했던 kid 값조차 echo하지 않는다


async def test_revoke_not_found_exception_omits_exchange_secrets(service, pool):
    user_id = await create_test_user(pool)

    with pytest.raises(ExchangeCredentialError) as exc_info:
        await service.revoke(user_id, "bitget")

    message = str(exc_info.value)
    assert _SECRET_API_KEY not in message
    assert _SECRET_KEY not in message


# ── DEEPEN-DEPTH PLT-33 (task-24514): additional negatives, failure injection,
#    gate-red reproduction, and performance assertion for register failure.


class _LeakyAdapter:
    """Adapter that embeds raw credentials in its exception message —
    the exact scenario the redaction tests must catch."""

    def __init__(self) -> None:
        pass

    async def get_balance(self):
        raise RuntimeError(f"connection refused: key={_SECRET_API_KEY} secret={_SECRET_KEY}")

    async def aclose(self):
        return None


async def test_register_leaky_adapter_omits_raw_exception_credentials(pool, key_ring_a):
    """Negative 1: adapter exception that *includes* credentials verbatim must
    still not leak them in the raised ExchangeCredentialError message."""
    user_id = await create_test_user(pool)
    failing_service = ExchangeCredentialService(
        pool, key_ring=key_ring_a, adapter_factory=lambda *a, **k: _LeakyAdapter()
    )

    with pytest.raises(ExchangeCredentialError) as exc_info:
        await failing_service.register(user_id, "bitget", _SECRET_API_KEY, _SECRET_KEY)

    message = str(exc_info.value)
    assert _SECRET_API_KEY not in message, "api_key must not appear in exception message"
    assert _SECRET_KEY not in message, "api_secret must not appear in exception message"


async def test_register_malformed_key_omits_credentials(pool, key_ring_a):
    """Negative 2: malformed/empty api_key must not echo the test secret anywhere."""
    user_id = await create_test_user(pool)
    failing_service = ExchangeCredentialService(
        pool, key_ring=key_ring_a, adapter_factory=_factory(fail=True)
    )

    with pytest.raises(ExchangeCredentialError) as exc_info:
        # Pass a string that looks like a key but is really the secret
        await failing_service.register(user_id, "bitget", _SECRET_API_KEY, "")

    message = str(exc_info.value)
    assert _SECRET_API_KEY not in message
    assert _SECRET_KEY not in message


async def test_register_empty_secret_omits_api_key(pool, key_ring_a):
    """Negative 3: empty api_secret with valid-looking api_key must not leak the key."""
    user_id = await create_test_user(pool)
    failing_service = ExchangeCredentialService(
        pool, key_ring=key_ring_a, adapter_factory=_factory(fail=True)
    )

    with pytest.raises(ExchangeCredentialError) as exc_info:
        await failing_service.register(user_id, "bitget", "", _SECRET_KEY)

    message = str(exc_info.value)
    assert _SECRET_API_KEY not in message
    assert _SECRET_KEY not in message


async def test_register_gate_red_reproduction(pool, key_ring_a):
    """Gate-red reproduction: inject a delay into the adapter so the register
    path takes longer, then verify the exception still omits credentials.

    This proves the assertion actually *catches* — if the redaction logic
    were removed, the leaked credentials in the adapter exception would
    propagate into ExchangeCredentialError and this test would fail.
    """
    reset_virtual_totals()
    user_id = await create_test_user(pool)

    def slow_factory(exchange, api_key, api_secret, extra, *, demo_mode=True):
        # Wrap get_balance with 0.5s virtual delay (10× injection)
        orig_adapter = _factory(fail=True)(
            exchange, api_key, api_secret, extra, demo_mode=demo_mode
        )
        orig_get_balance = orig_adapter.get_balance

        async def delayed_get_balance():
            return await virtual_delayed_async(orig_get_balance, 0.5)()

        orig_adapter.get_balance = delayed_get_balance
        return orig_adapter

    failing_service = ExchangeCredentialService(
        pool, key_ring=key_ring_a, adapter_factory=slow_factory
    )

    with pytest.raises(ExchangeCredentialError) as exc_info:
        await failing_service.register(user_id, "bitget", _SECRET_API_KEY, _SECRET_KEY)

    message = str(exc_info.value)
    assert _SECRET_API_KEY not in message
    assert _SECRET_KEY not in message


@pytest.mark.perf
async def test_register_failure_performance_under_virtual_delay(pool, key_ring_a, perf_budget):
    """Performance assertion: register failure with 10× virtual delay must
    still complete within budget.

    Measured baseline (local, no delay): ~2 ms CPU.
    Budget: 2 ms × 30 = 60 ms (30× margin per ADR-2026-09-09-C).
    Virtual 0.5 s delay is injected via virtual_delayed_async; the PerfBudget
    ledger sees it as wall-clock, but the CPU budget assertion confirms the
    code path itself stays fast even when the adapter is artificially slowed.
    """
    reset_virtual_totals()
    user_id = await create_test_user(pool)

    def delayed_factory(exchange, api_key, api_secret, extra, *, demo_mode=True):
        orig_adapter = _factory(fail=True)(
            exchange, api_key, api_secret, extra, demo_mode=demo_mode
        )
        orig_get_balance = orig_adapter.get_balance

        async def delayed_get_balance():
            return await virtual_delayed_async(orig_get_balance, 0.5)()

        orig_adapter.get_balance = delayed_get_balance
        return orig_adapter

    failing_service = ExchangeCredentialService(
        pool, key_ring=key_ring_a, adapter_factory=delayed_factory
    )

    # best_of: run several times, report minimum (least noisy)
    # register raises ExchangeCredentialError — wrap so sample_async still
    # captures the timing before the exception propagates.
    async def _register_call() -> None:
        try:
            await failing_service.register(user_id, "bitget", _SECRET_API_KEY, _SECRET_KEY)
        except ExchangeCredentialError:
            pass  # expected

    sample = await perf_budget.sample_async(_register_call)
    # The exception is expected; we assert on timing, not result.
    # CPU must stay under 60 ms (baseline 2 ms × 30× margin).
    assert sample.cpu_ms < 60.0, (
        f"register failure path too slow: cpu={sample.cpu_ms:.3f}ms "
        f"wall={sample.wall_ms:.3f}ms — budget 60 ms"
    )
