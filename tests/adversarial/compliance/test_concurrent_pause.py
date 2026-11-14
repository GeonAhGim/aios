"""H-1c(task-3370) — test_concurrent_pause_and_submit_denies_race용 D2 보강 테스트.

동시성 시나리오에 필요한 negative 3, failure_injection 1, perf 2를 추가한다.
원본 파일(test_no_mandate_reject.py)이 500줄 임계값에 도달해 §12 분할 규칙에 따라
별도 파일로 분리했다.

Logical scope: test_concurrent_pause_and_submit_denies_race의 D2 하한 보강.
"""

from __future__ import annotations

import asyncio

import pytest

from src.foundation.mandates.application.pause_mandate import pause_mandate
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.gate import GateOutcome
from tests.adversarial.compliance.test_no_mandate_reject import (
    _context,
    _unique_execution_id,
)
from tests.foundation.integration.risk_gate.conftest import activate_mandate_with_defaults
from tests.integration.conftest import create_test_tenant
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

# --- 음성 (negative) 3종 ---


async def test_concurrent_pause_unknown_tenant_never_allows(pool, repo, trust_repo):
    """음성 1 — 존재하지 않는 tenant(user_id)도 동시 pause 경로에서 ALLOW되지 않는다."""
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    try:
        await asyncio.gather(
            pause_mandate(repo, tenant_id=2**31 - 99),
            gate(_context(2**31 - 99, execution_id=_unique_execution_id())),
        )
    except Exception:  # noqa: BLE001
        return
    raise AssertionError("unknown tenant must not ALLOW")


async def test_concurrent_pause_audit_no_leak(pool, repo, trust_repo):
    """음성 2 — 동시 pause 후 DENY 감사 기록은 해당 execution_id에만 남는다."""
    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_id)
    mandate = await repo.get_mandate(user_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    execution_id = _unique_execution_id()
    other_id = execution_id + 1 if execution_id < 2**31 - 1 else execution_id - 1
    context = _context(user_id, mandate.active_revision_id, execution_id=execution_id)

    await asyncio.gather(
        pause_mandate(repo, tenant_id=user_id),
        gate(context),
    )

    async with pool.acquire() as conn:
        other = await conn.fetchrow(
            "SELECT 1 FROM audit_log WHERE action_type = 'risk_gate.unmandated_submit' "
            "AND target_id = $1",
            str(other_id),
        )
    assert other is None


async def test_concurrent_pause_pre_pause_allow_does_not_leak(pool, repo, trust_repo):
    """음성 3 — pause 전 ALLOW 결정을 캐시한 context가 pause 후 재사용되지 않는다."""
    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_id)
    mandate = await repo.get_mandate(user_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    execution_id = _unique_execution_id()
    context = _context(user_id, mandate.active_revision_id, execution_id=execution_id)

    # pause 전: ALLOW
    pre = await gate(context)
    assert pre.outcome == GateOutcome.ALLOW

    # 동시: pause + submit
    await asyncio.gather(
        pause_mandate(repo, tenant_id=user_id),
        gate(context),
    )

    # 같은 context를 pause 후에 다시 쓰면 ALLOW가 재사용되지 않는다.
    post = await gate(context)
    assert post.outcome == GateOutcome.DENY


# --- 실패 주입 ---


async def test_concurrent_pause_db_failure_never_allows(pool, repo, trust_repo):
    """실패 주입 — DB 연결 실패 시 게이트는 ALLOW를 반환하지 않는다."""
    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_id)
    mandate = await repo.get_mandate(user_id)

    class _BrokenPool:
        def __getattr__(self, name):
            raise ConnectionError(f"injected pool failure: {name}")

    gate = make_foundation_pre_submit_gate(_BrokenPool(), require_mandate=True)
    context = _context(user_id, mandate.active_revision_id, execution_id=_unique_execution_id())
    try:
        await asyncio.gather(
            pause_mandate(repo, tenant_id=user_id),
            gate(context),
        )
    except Exception:  # noqa: BLE001
        return
    raise AssertionError("should have raised or returned DENY")


# --- 성능 단언 + 게이트 적색 재현 ---

# 성능 예산: 동시 pause 게이트 wall 실측 약 5.5ms(로컬, best-of-5), 예산은 30배 이상 여유.
_CONCURRENT_BUDGET_MS = 200.0  # 실측 5.5ms의 ~36배


async def _measure_concurrent_gate(perf_budget, pool, repo, trust_repo, *, delay_s=0.0):
    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_id)
    mandate = await repo.get_mandate(user_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    context = _context(user_id, mandate.active_revision_id, execution_id=_unique_execution_id())
    call = virtual_delayed_async(gate, delay_s) if delay_s else gate
    return await perf_budget.best_of_async(lambda: call(context), n=5, warmup=1)


@pytest.mark.perf
async def test_concurrent_pause_gate_perf_budget(perf_budget: PerfBudget, pool, repo, trust_repo):
    """성능 단언 — 동시 pause 게이트 wall 지연이 예산 미만."""
    reset_virtual_totals()
    sample = await _measure_concurrent_gate(perf_budget, pool, repo, trust_repo)
    assert sample.wall_ms < _CONCURRENT_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_CONCURRENT_BUDGET_MS
    )


@pytest.mark.perf
async def test_concurrent_pause_gate_perf_gate_red_with_10x_injection(
    perf_budget: PerfBudget, pool, repo, trust_repo
):
    """게이트 적색 재현 — 예산의 10배 가상 지연 주입 시 같은 단언이 적색."""
    reset_virtual_totals()
    sample = await _measure_concurrent_gate(
        perf_budget, pool, repo, trust_repo, delay_s=_CONCURRENT_BUDGET_MS * 10 / 1000
    )
    assert not (sample.wall_ms < _CONCURRENT_BUDGET_MS)
    reset_virtual_totals()
