"""FA-5 `application/resolve_context.py` 실DB 성능 회귀 테스트.

DEPTH 감사(task-2724, docs/audit/DEPTH_FA.md)가 원 task-1795(FA-5, 커밋
3fc5123)를 D3 하한 미달로 판정한 마지막 공백(negative≥8·실패주입·게이트
재현·D3 우회시뮬/50동시워커 증거는 이미 충족, 수치 성능 단언만 없음)을
메운다(task-3009). `resolve_context()`는 모든 주문·포지션·원장 쓰기 진입점이
호출하는 단일 컨텍스트 해석 진입점이라(§2.1 application 행) 4단 계층 각각에
SELECT 한 번씩, 호출마다 4회의 실 DB 라운드트립이 고정 비용으로 든다 —
회귀가 생기면(예: 조회 하나가 테이블 스캔으로 바뀌는 등) 이 비용이 자란다.

공유 TEST_DATABASE_URL의 절대 지연 변동성 때문에 절대 ms 임계 대신, 가벼운
baseline 호출 1건 대비 정규화한 상한만 게이트로 쓴다(task-3003/3004 선례와
동일 교훈, 그 커밋들이 인용한 LA-18 test_quality_metrics.py·LA-24
test_market_data_router.py까지 거슬러 올라가는 관례).
"""

from __future__ import annotations

import pytest

from src.foundation.entities.application.resolve_context import (
    ResolveContextRequest,
    resolve_context,
)
from tests.conftest import PerfBudget
from tests.integration.conftest import create_test_tenant


@pytest.mark.perf
async def test_resolve_context_p95_latency_stays_within_normalized_ceiling(
    pool, repo, perf_budget: PerfBudget
) -> None:
    # `create_test_tenant()` persists the FA-1 default hierarchy by default
    # (see its docstring) -- `resolve_context()`'s deterministic ids resolve
    # against it without any extra seeding step.
    tenant_id = await create_test_tenant(pool)
    request = ResolveContextRequest(tenant_id=tenant_id, user_id=tenant_id)

    # baseline: 비동기 I/O를 재는 테스트는 perf_budget.sample_async() 사용
    baseline = await perf_budget.sample_async(lambda: resolve_context(repo, request))

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
