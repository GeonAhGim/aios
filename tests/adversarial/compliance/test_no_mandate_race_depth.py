"""task-24421 DEEPEN-DEPTH H-1c — test_no_mandate_reject.py race 테스트 D2 보강
(negative 3 / 실패 주입 1 / perf 예산 + 10x 지연 적색 재현).

원본 파일이 500줄 한도에 닿아 있어 형제 파일로 분리했다(CLAUDE.md §6-12)."""

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


async def _warmed_race_setup(pool, repo, trust_repo, *, with_revision: bool = True):
    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    revision_id = (await repo.get_mandate(user_id)).active_revision_id if with_revision else None
    context = _context(user_id, revision_id, execution_id=_unique_execution_id())
    warm = await gate(context)
    assert warm.outcome == GateOutcome.ALLOW  # 경합 전 캐시를 데운다
    return user_id, gate, context


async def test_concurrent_pause_race_many_submits_all_deny_after_commit(pool, repo, trust_repo):
    """negative 1 — pause와 제출 5건 동시 진입 뒤 반복 제출은 전부 DENY(PAUSED)."""
    user_id, gate, context = await _warmed_race_setup(pool, repo, trust_repo)

    results = await asyncio.gather(
        pause_mandate(repo, tenant_id=user_id), *[gate(context) for _ in range(5)]
    )
    assert results[0].state.value == "PAUSED"
    decisions = [await gate(context) for _ in range(3)]
    assert all(d.outcome == GateOutcome.DENY for d in decisions)
    assert all("STATE_MANDATE_PAUSED" in d.reason_codes for d in decisions)


async def test_concurrent_double_pause_and_submit_still_denies(pool, repo, trust_repo):
    """negative 2 — pause가 중복 동시 호출(멱등성 경합)돼도 이후 제출은 DENY."""
    user_id, gate, context = await _warmed_race_setup(pool, repo, trust_repo)

    first, second, _race = await asyncio.gather(
        pause_mandate(repo, tenant_id=user_id),
        pause_mandate(repo, tenant_id=user_id),
        gate(context),
        return_exceptions=True,
    )
    paused = [r for r in (first, second) if not isinstance(r, BaseException)]  # 충돌 예외는 정당
    assert paused and all(r.state.value == "PAUSED" for r in paused)
    decision = await gate(context)
    assert decision.outcome == GateOutcome.DENY
    assert "STATE_MANDATE_PAUSED" in decision.reason_codes


async def test_concurrent_pause_race_without_revision_id_denies(pool, repo, trust_repo):
    """negative 3 — revision id 없는 제출(resolver 경로)도 pause 경합 뒤 DENY."""
    user_id, gate, context = await _warmed_race_setup(pool, repo, trust_repo, with_revision=False)

    await asyncio.gather(pause_mandate(repo, tenant_id=user_id), gate(context))
    decision = await gate(context)
    assert decision.outcome == GateOutcome.DENY


async def test_concurrent_pause_race_db_failure_never_allows(pool, repo, trust_repo):
    """실패 주입 — pause 경합 뒤 DB 연결 획득이 실패해도 게이트는 ALLOW를
    반환하지 않는다(예외 전파 또는 DENY)."""
    user_id, _, context = await _warmed_race_setup(pool, repo, trust_repo)
    await pause_mandate(repo, tenant_id=user_id)

    class _BrokenPool:
        def __getattr__(self, name):
            raise ConnectionError(f"injected pool failure: {name}")

    broken_gate = make_foundation_pre_submit_gate(_BrokenPool(), require_mandate=True)
    try:
        decision = await broken_gate(context)
    except Exception:  # noqa: BLE001 - propagation is fail-closed
        return
    assert decision.outcome == GateOutcome.DENY


# 성능 예산(task-24421): pause 경합 뒤 게이트 wall 실측 약 5ms(로컬, best-of-5)의 30배 이상 여유.
_RACE_BUDGET_MS = 200.0  # 실측 약 5ms의 ~40배


async def _measure_post_race_gate(
    perf_budget: PerfBudget, pool, repo, trust_repo, *, delay_s: float = 0.0
):
    user_id, gate, context = await _warmed_race_setup(pool, repo, trust_repo)
    await asyncio.gather(pause_mandate(repo, tenant_id=user_id), gate(context))
    call = virtual_delayed_async(gate, delay_s) if delay_s else gate

    async def _run():
        return await call(context)

    return await perf_budget.best_of_async(_run, n=5, warmup=1)


@pytest.mark.perf
async def test_concurrent_pause_race_gate_perf_budget(
    perf_budget: PerfBudget, pool, repo, trust_repo
):
    """성능 단언 — pause 경합 뒤 게이트 wall 지연이 예산 미만."""
    reset_virtual_totals()
    sample = await _measure_post_race_gate(perf_budget, pool, repo, trust_repo)
    assert sample.wall_ms < _RACE_BUDGET_MS, perf_budget.describe(sample, budget_ms=_RACE_BUDGET_MS)


@pytest.mark.perf
async def test_concurrent_pause_race_gate_perf_gate_red_with_10x_injection(
    perf_budget: PerfBudget, pool, repo, trust_repo
):
    """게이트 적색 재현 — 예산의 10배 가상 지연을 주입하면 같은 단언이 적색이 된다."""
    reset_virtual_totals()
    sample = await _measure_post_race_gate(
        perf_budget, pool, repo, trust_repo, delay_s=_RACE_BUDGET_MS * 10 / 1000
    )
    assert not (sample.wall_ms < _RACE_BUDGET_MS)
    reset_virtual_totals()
