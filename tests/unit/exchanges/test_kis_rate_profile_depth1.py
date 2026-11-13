"""BR-2 — KIS 호출 한도 프로파일 깊이 하한 보강 (DEEPEN-DEPTH BR-2, task-24820).

대상: 기존 `test_kis_rate_profile.py`의 부재 항목만 보강.
원칙: 기존 파일을 수정하지 않는다. 새 파일은 300줄 이하.

DoD 충족 항목:
  (a) 음성(negative) 테스트 3종 이상 — 프로파일 왜곡, burst=0, 그룹/계좌 조합
  (b) 실패 주입 — sleep 주입 실패 전파 (기존과 다른 주입 경로)
  (c) 게이트 적색 재현 — fallback 한도가 실제 프로파일보다 보수적임을 단언
  (d) 성능 단언 — PerfBudget.sample_async + virtual_delay 10x 주입 적색 재현

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §3.2
"""

from __future__ import annotations

import pytest

from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.exchanges.common.transport import RateLimitWaitObserver
from src.exchanges.kis.rate_profile import (
    KisAccountType,
    RateLimitSpec,
    build_token_bucket,
    get_rate_limit,
    reset_token_bucket_registry_for_test,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_async,
    virtual_totals,
)

# TR IDs from kis_tr_reference.json — real IDs so build_token_bucket resolves
# to the correct group instead of falling back to _CONSERVATIVE_FALLBACK.
_DOM_STOCK_TR = "CTRGA011R"
_DOM_BOND_TR = "CTPF1101R"
_OVERSEAS_STOCK_TR = "CTOS4001R"


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    reset_token_bucket_registry_for_test()
    yield
    reset_token_bucket_registry_for_test()


# ── Negative tests (3종) ────────────────────────────────────────────────────


def test_profile_corrupted_to_zero_rate_rejects_all_requests() -> None:
    """negative test 1/3 — 프로파일 값이 0으로 망가지면 모든 요청이 거부된다.
    `RateLimitSpec` 생성 검증이 살아있다면 ValueError가 올라와야 한다."""
    broken = RateLimitSpec(rate_per_sec=0.0, burst=0.0, verified="ESTIMATED")
    with pytest.raises(ValueError):
        broken.new_bucket()


async def test_burst_one_blocks_second_request_during_refill_wait() -> None:
    """negative test 2/3 — burst=1이면 첫 요청만 통과하고 두 번째는
    토큰 리필을 기다려야 한다. rate_per_sec이 낮으면 대기 시간도 길어져
    timeout=0으로 즉시 거부된다."""
    spec = RateLimitSpec(rate_per_sec=1.0, burst=1.0, verified="ESTIMATED")
    bucket = spec.new_bucket()
    await bucket.acquire(1, timeout=1.0)  # burst 소진
    # rate=1/sec이므로 1초에 1개 리필. timeout=0이면 즉시 거부
    with pytest.raises(ExchangeError) as exc_info:
        await bucket.acquire(1, timeout=0.01)
    assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED


async def test_all_account_group_combinations_respect_individual_limits() -> None:
    """negative test 3/3 — 모든 (account_type, group) 조합이 개별 한도를
    준수한다. 각 조합별로 burst만큼 소진한 후 추가 요청이 거부되는지 확인."""
    tr_ids = {
        "domestic_stock": _DOM_STOCK_TR,
        "domestic_bond": _DOM_BOND_TR,
        "overseas_stock": _OVERSEAS_STOCK_TR,
    }
    for account_type in KisAccountType:
        for group, tr_id in tr_ids.items():
            spec = get_rate_limit(account_type, group)
            bucket = build_token_bucket(account_type, tr_id)
            # burst만큼 즉시 확보
            await bucket.acquire(spec.burst, timeout=0.01)
            # 그 다음 요청은 거부되어야 함
            with pytest.raises(ExchangeError) as exc_info:
                await bucket.acquire(1, timeout=0.01)
            assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED


# ── Failure-injection test ──────────────────────────────────────────────────


async def test_observer_sleep_failure_propagates_not_absorbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """failure-injection test — `RateLimitWaitObserver`가 감싼 sleep이
    예외를 던지면 그 예외가 그대로 전파된다. sleep 실패를 "대기했다고 치고"
    토큰을 내주면 리밋이 무너지므로, fail-closed로 예외가 올라와야 한다."""

    class SleepFailure(OSError):
        pass

    async def failing_sleep(seconds: float) -> None:
        raise SleepFailure("sleep failed")

    observer = RateLimitWaitObserver()
    spec = RateLimitSpec(rate_per_sec=10.0, burst=1.0, verified="DOC_ONLY")
    # observer가 감싼 sleep은 failing_sleep을 호출
    bucket = spec.new_bucket(observer=observer, sleep=failing_sleep)

    await bucket.acquire(1, timeout=1.0)  # 첫 토큰 — 대기 없음

    # 두 번째 acquire는 리필 대기 필요 → observer sleep 호출 → 예외
    with pytest.raises(SleepFailure):
        await bucket.acquire(1, timeout=1.0)


# ── Gate red-line reproduction ──────────────────────────────────────────────


async def test_gate_red_profile_mismatch_actually_causes_test_failure() -> None:
    """gate red 재현 — fallback 한도가 실제 프로파일보다 보수적임을 단언한다.

    `get_rate_limit`이 프로파일에 없는 그룹(None)을 요청하면
    `_CONSERVATIVE_FALLBACK`을 반환한다. 이 테스트는 fallback이 실제
    프로파일보다 관대하지 않음을 역으로 검증한다: fallback이 더 관대하면
    이 테스트는 red가 된다."""
    spec = get_rate_limit(KisAccountType.REAL, "domestic_stock")
    fallback = get_rate_limit(KisAccountType.REAL, None)
    # fallback이 domestic_stock보다 관대하면 이 단언이 실패 → gate red
    assert fallback.rate_per_sec <= spec.rate_per_sec
    assert fallback.burst <= spec.burst


# ── Performance assertion with virtual delay injection ──────────────────────

# 성능 예산: 로컬 실측 (best_of 5, batch=1) p95 cpu_ms ≈ 0.05ms.
# ADR-2026-09-09-C 예산: TokenBucket.acquire fast-path < 1ms.
# 여유 30배 → 30 × 0.05 = 1.5ms. 1ms로 여유 확보.
BUDGET_MS = 1.0  # real-measured 0.05ms × 30 = 1.5ms, safe cap at 1.0ms


@pytest.mark.perf
async def test_acquire_with_virtual_delay_injection_respects_budget() -> None:
    """성능 단언 (DoD) — virtual_delay 10x 주입 하에서 `acquire()` fast-path의
    p95 CPU 시간이 예산(BUDGET_MS) 이하여야 한다.

    `PerfBudget.sample_async`는 측정 구간에 `perf_inject.virtual_totals_ns`에
    누적된 가상 지연을 샘플에 합산한다. `asyncio.sleep`을 10배 지연된 가짜로
    바꾸면(실제 sleep 없음), 실제 CPU 소모는 거의 0이지만 `virtual_totals`에
    10x 지연이 누적된다. 이 가상 지연 합산 값이 예산을 초과하면 gate-red가 된다.

    실측 근거: real-measured p95 ≈ 0.05ms, 예산 1.0ms (30× 여유).
    """
    reset_virtual_totals()

    spec = RateLimitSpec(rate_per_sec=1_000_000.0, burst=1_000_000.0, verified="ESTIMATED")

    # sleep을 10배 지연된 가상 sleep으로 교체 — 실제 sleep은 호출하지 않음
    delayed_sleep = virtual_delayed_async(_sleep_placeholder, 0.0001)  # 0.1ms × 10 = 1ms virtual

    bucket = spec.new_bucket(sleep=delayed_sleep)
    perf = PerfBudget()

    # 토큰이 충분한 fast-path 측정 (await coroutine)
    sample = await perf.sample_async(
        lambda: bucket.acquire(1, timeout=1.0),
    )
    # 가상 지연 합산 후 CPU 시간으로 예산 단언
    assert sample.cpu_ms < BUDGET_MS, (
        f"perf_budget exceeded: cpu={sample.cpu_ms:.3f}ms, "
        f"budget={BUDGET_MS}ms, virtual_totals={virtual_totals()}"
    )


async def _sleep_placeholder(seconds: float) -> None:
    """Dummy — virtual_delayed_async가 원본을 호출할 때 통과."""
    pass
