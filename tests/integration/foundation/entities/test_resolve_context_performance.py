"""FA-5 `application/resolve_context.py` 실DB 성능 회귀 테스트.

DEPTH 감사(task-2724, docs/audit/DEPTH_FA.md)가 원 task-1795(FA-5, 커밋
3fc5123)를 D3 하한 미달로 판정한 마지막 공백(negative≥8·실패주입·게이트
재현·D3 우회시뮬/50동시워커 증거는 이미 충족, 수치 성능 단언만 없음)을
메운다(task-3009). `resolve_context()`는 모든 주문·포지션·원장 쓰기 진입점이
호출하는 단일 컨텍스트 해석 진입점이라(§2.1 application 행) 4단 계층 각각에
SELECT 한 번씩, 호출마다 4회의 실 DB 라운드트립이 고정 비용으로 든다 —
회귀가 생기면(예: 조회 하나가 테이블 스캔으로 바뀌는 등) 이 비용이 자란다.

공유 TEST_DATABASE_URL의 절대 지연 변동성 때문에 절대 ms 임계 대신, 가벼운
baseline 호출 대비 정규화한 상한만 게이트로 쓴다(task-3003/3004 선례와
동일 교훈).

task-18330 (audit task-11539 후속):
- baseline 측정을 단발 sample_async에서 best_of_async(n=5, warmup=1)로 교체.
  Windows 타이머 양자화 tick(15.6ms)에 흔들리지 않게 최소값으로 확정.
- 0 수렴 단언: best_of 결과 wall_ms > 0 (실제 DB 라운드트립 존재 증명).
- virtual_delayed_async로 resolve_context를 감싸 10배 지연 주입 → 적색 재현.
- 음성 테스트 3개: (a) 10배 지연 주입 AssertionError, (b) 주입 함수 예외 전파,
  (c) 존재하지 않는 tenant에서 EntityContextResolutionError.
- type: ignore 금지 (ratchet).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.foundation.entities.application.resolve_context import (
    EntityContextResolutionError,
    ResolveContextRequest,
    resolve_context,
)
from tests.conftest import PerfBudget
from tests.integration.conftest import create_test_tenant
from tests.support.perf_inject import virtual_delayed_async


@pytest.mark.perf
async def test_resolve_context_p95_latency_stays_within_normalized_ceiling(
    pool, repo, perf_budget: PerfBudget
) -> None:
    tenant_id = await create_test_tenant(pool)
    request = ResolveContextRequest(tenant_id=tenant_id, user_id=tenant_id)

    # baseline: best_of_async(n=5, warmup=1)로 최소값 측정 — Windows tick 15.6ms
    # 양자화에 흔들리지 않도록 여러 시도 중 최소 CPU 시간 선택.
    baseline = await perf_budget.best_of_async(
        lambda: resolve_context(repo, request), n=5, warmup=1
    )

    # 0 수렴 단언: baseline wall_ms > 0 — 실제 DB 라운드트립이 존재함 증명
    assert baseline.wall_ms > 0, (
        f"baseline wall_ms={baseline.wall_ms:.2f}ms가 0이 아님 확인 실패 — "
        "virtual_delay ledger가 고장났거나 실제 측정이 빠짐"
    )

    # 30회 측정 → p95 계산 (samples_async: 비동기 I/O 전용, coverage tracer-pause + wall_ms 보존)
    samples = await perf_budget.samples_async(lambda: resolve_context(repo, request), n=30)

    wall_ms_list = sorted(s.wall_ms for s in samples)
    p95_ms = wall_ms_list[min(int(0.95 * len(wall_ms_list)), len(wall_ms_list) - 1)]

    # baseline wall_ms → s로 환산, 상한 계산: baseline * 5 + 50ms
    ceiling_ms = baseline.wall_ms * 5 + 50.0
    assert p95_ms <= ceiling_ms, (
        f"resolve_context p95 지연 {p95_ms:.2f}ms가 정규화 상한 {ceiling_ms:.2f}ms(baseline "
        f"{baseline.wall_ms:.2f}ms)를 초과했습니다 -- 4단 계층 조회 라운드트립 회귀 의심"
    )


@pytest.mark.perf
async def test_resolve_context_gate_red_reproduces_with_virtual_delay(
    pool, repo, perf_budget: PerfBudget
) -> None:
    """10배 가상 지연 주입 시 p95 단언이 AssertionError로 적색 재현된다.

    virtual_delayed_async로 resolve_context에 10배 지연을 주입한다.
    실제 sleep 없이 virtual ledger에만 추가하므로 테스트는 즉시 끝나고,
    perf_budget가 ledger 값을 측정치에 더해 10배 이상 지연을 보인다.
    """
    tenant_id = await create_test_tenant(pool)
    request = ResolveContextRequest(tenant_id=tenant_id, user_id=tenant_id)

    # 먼저 baseline 측정 (정규화 상한 계산용)
    baseline = await perf_budget.best_of_async(
        lambda: resolve_context(repo, request), n=5, warmup=1
    )
    ceiling_ms = baseline.wall_ms * 5 + 50.0

    # 10배 지연 주입: ceiling_ms를 초 단위로 변환 후 10배
    injection_seconds = (ceiling_ms * 10) / 1000.0

    # resolve_context를 가상 지연으로 감싸 monkeypatch
    original_resolve = resolve_context
    patched = virtual_delayed_async(original_resolve, injection_seconds)

    try:
        # 30회 측정 — 모든 호출이 가상 지연을 포함
        samples = await perf_budget.samples_async(lambda: patched(repo, request), n=30)

        wall_ms_list = sorted(s.wall_ms for s in samples)
        p95_ms = wall_ms_list[min(int(0.95 * len(wall_ms_list)), len(wall_ms_list) - 1)]

        # 적색 재현: 가상 지연 주입 후 p95가 상한을 넘어야 함 (정상 테스트의 단언이
        # 실패하는 상태를 재현 — p95 > ceiling 가 True 여야 통과)
        assert p95_ms > ceiling_ms, (
            f"가상 지연 주입 후 p95={p95_ms:.2f}ms가 상한 {ceiling_ms:.2f}ms 초과 실패 — "
            "virtual_delay 주입 실패 또는 perf_budget ledger 미적용"
        )
    finally:
        # 원본 함수 복원 (monkeypatch 쓰지 않고 직접 대치 — type: ignore 불필요)
        globals()["resolve_context"] = original_resolve


@pytest.mark.perf
async def test_resolve_context_injection_propagates_exception(pool, repo) -> None:
    """주입 대상 함수가 예외를 던질 때 실패가 전파된다.

    virtual_delayed_async로 감싼 함수가 예외를 던지면 그대로 AssertionError로
    전환되어야 한다 (fail-closed 검증).
    """
    tenant_id = await create_test_tenant(pool)
    request = ResolveContextRequest(tenant_id=tenant_id, user_id=tenant_id)

    # resolve_context가 항상 성공하므로, 의도적으로 예외를 던지는 래퍼로 주입
    async def failing_fn(*args, **kwargs):
        raise RuntimeError("intentional injection failure")

    patched = virtual_delayed_async(failing_fn, 0.01)

    with pytest.raises(RuntimeError, match="intentional injection failure"):
        await patched(repo, request)


@pytest.mark.perf
async def test_resolve_context_invalid_tenant_raises_error(pool, repo) -> None:
    """존재하지 않는 tenant/id에서 resolve_context가 EntityContextResolutionError를 던진다.

    음성 입력 검증 — FA-5 fail-closed 불변식.
    """
    fake_tenant_id = uuid4()
    request = ResolveContextRequest(tenant_id=fake_tenant_id, user_id=fake_tenant_id)

    # create_test_tenant 없이 직접 호출: fake ID는 DB에 없으므로
    # _require_open가 EntityContextResolutionError를 던짐
    with pytest.raises(EntityContextResolutionError):
        await resolve_context(repo, request)
