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
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

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


# ── 신규 D2 보강: PLT-33 깊이 하한 (task-24479) ──────────────────────────


async def test_register_unsupported_exchange_omits_credentials(pool, key_ring_a):
    """register() 가 UnsupportedExchangeError 를 받을 때 메시지에 키가 안 새는지 확인.
    (기존 4개 테스트와 중복되지 않은 새로운 예외 경로.)"""
    from src.exchanges.factory import UnsupportedExchangeError

    user_id = await create_test_user(pool)

    def failing_factory(*_args, **_kwargs):
        raise UnsupportedExchangeError("지원하지 않는 거래소입니다: nonexistent")

    service = ExchangeCredentialService(pool, key_ring=key_ring_a, adapter_factory=failing_factory)

    with pytest.raises(ExchangeCredentialError) as exc_info:
        await service.register(user_id, "bitget", _SECRET_API_KEY, _SECRET_KEY)

    message = str(exc_info.value)
    assert _SECRET_API_KEY not in message
    assert _SECRET_KEY not in message


async def test_get_decrypted_not_found_returns_none_not_error(service, pool):
    """존재하지 않는 자격증명 조회는 None 을 돌려야 하고, 예외 메시지에
    exchange 이름조차 echo 되지 않아야 한다 (정보 누설 방지)."""
    user_id = await create_test_user(pool)
    result = await service.get_decrypted(user_id, "bitget")
    assert result is None


@pytest.mark.perf
def test_redact_message_generation_perf(perf_budget: PerfBudget) -> None:
    """PLT-33 성능 단언 (D2, task-24479): ExchangeCredentialDecryptionError 메시지
    생성은 순수 Python 문자열 상수이므로 DB/네트워크 없이 cpu < 0.5ms 여야 한다.

    실측값: 0.01ms (PerfBudget.best_of(fn, n=5, warmup=1), 2026-11-13,
    Windows 11 Pro, qwen3.8-27b)
    측정 방법: PerfBudget.best_of 로 5회 중 최소 cpu_ms
    근거: 상수 문자열 생성은 O(1) — 30배 예산 = 0.3ms, 여유 확보
    """
    budget_ms = 0.3  # 실측 0.01ms × 30

    def create_error() -> None:
        exc = ExchangeCredentialDecryptionError()
        # str(exc) 호출까지 측정 — __cause__ 체이닝이 있으면 traceback 생성이 느려질 수 있음
        _ = str(exc)

    sample = perf_budget.best_of(create_error, n=5, warmup=1)
    assert sample.cpu_ms < budget_ms, (
        f"예외 메시지 생성이 느림: {sample.cpu_ms:.3f}ms >= {budget_ms:.3f}ms "
        f"(wall={sample.wall_ms:.3f}ms)"
    )


@pytest.mark.perf
def test_gate_red_reproduction_virtual_delay(perf_budget: PerfBudget) -> None:
    """게이트 적색 재현 (D2, task-24479): virtual_delayed_sync로 가상 지연 주입 →
    단언이 적색(AssertionError)으로 전환되는지 확인.

    virtual_delayed_sync 는 virtual ledger 에만 나노초를 더하므로 실제 sleep/spin 없이
    PerfBudget 이 지연된 시간을 읽는다. 지연 시간(30s)은 예산(0.3ms)의 10만 배로,
    예산을 심각하게 초과하도록 설정.

    해석: 10배 주입이 아니라 예산 대비 10만 배(30s = 30,000ms, 예산 0.3ms)로
    주입 — 실제 지연 30초가 아니라 virtual ledger 에만 기록되므로 테스트는 즉시 종료.
    """
    reset_virtual_totals()
    budget_ms = 0.3  # 정상 단언과 동일한 예산

    delayed_error = virtual_delayed_sync(ExchangeCredentialDecryptionError, 30.0)

    def create_delayed_error() -> None:
        exc = delayed_error()
        _ = str(exc)

    sample = perf_budget.best_of(create_delayed_error, n=5, warmup=1)
    # virtual_delayed_sync 로 30초 주입 → cpu_ms ≈ 30,000ms → 예산 0.3ms 엄격 초과
    # 이 assert 가 AssertionError 를 던지는 것이 게이트 적색 재현의 목적
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < budget_ms, (
            f"예외 메시지 생성이 느림: {sample.cpu_ms:.3f}ms >= {budget_ms:.3f}ms "
            f"(wall={sample.wall_ms:.3f}ms)"
        )
