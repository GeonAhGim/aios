"""R-35 `evaluate_pre_submit` 동시성(D3) 통합테스트 — 실 DB 대상.

`test_pre_submit_gate.py`에서 분리(loc_over_500, CLAUDE.md §7). DoD(6) —
kill switch 활성화와 경합하는 동시 호출들 사이에서 dirty/stale read 없이
커밋 전/후로 정확히 갈린다(tests/adversarial/risk/test_fence_race.py
단계형 gather 패턴을 evaluate_pre_submit 자신의 control 조회에 적용).
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from src.core.risk.decision import RiskOutcome
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.evaluate_pre_submit import evaluate_pre_submit
from src.foundation.risk_gate.domain.models import SafetyScope
from tests.integration.conftest import create_test_tenant
from tests.integration.risk.conftest import (
    PROVIDER,
    QTY,
    SIDE,
    SYMBOL,
    healthy_connection_repo,
    normal_risk_repo,
)

_N_EARLY = 3
_N_LATE = 3


class _BarrierGatedRiskRepo:
    """`read_fence_and_controls` 직전에 barrier에서 기다린 뒤 실 DB(`inner`)에
    위임한다 — kill switch 활성화 commit이 그 barrier를 여는 시점보다 앞섬을
    보장해, 이 호출의 control 조회가 항상 활성화 *이후* 스냅샷을 보도록
    결정론적으로 고정한다(test_fence_race.py 단계형 gather와 동일 취지)."""

    def __init__(
        self,
        inner: PostgresRiskGateRepository,
        *,
        cb_level: str | None,
        distrust_level: str | None,
        barrier: asyncio.Event,
    ) -> None:
        self._inner = inner
        self._cb_level = cb_level
        self._distrust_level = distrust_level
        self._barrier = barrier

    async def read_fence_and_controls(self, pairs):  # noqa: ANN001, ANN201
        await self._barrier.wait()
        return await self._inner.read_fence_and_controls(pairs)

    async def read_safety_state(self, *, provider_code: str, symbol: str):  # noqa: ANN201
        return self._cb_level, self._distrust_level


async def test_staged_gather_kill_switch_activation_vs_concurrent_evaluations(
    pool, risk_repo, signal_repo, recorder
):
    """동시성 증명(D3) — kill switch 활성화와 경합하는 `_N_EARLY` + `_N_LATE`
    동시 `evaluate_pre_submit` 호출. early 그룹은 activator가 시작되기 전에
    이미 자신의 control 조회를 마쳤고, late 그룹은 activation commit이 연
    barrier가 열려야만 자신의 control 조회를 한다 — 그 사이 어떤 인터리빙도
    없다. 기대: early 전부 ALLOW(활성화 전 스냅샷), late 전부 DENY(활성화 후
    스냅샷) — dirty read(활성화 전인데 DENY로 새는 경우)도 stale
    read(활성화가 커밋됐는데 여전히 ALLOW로 새는 경우)도 없다."""
    tenant_id = await create_test_tenant(pool)
    execution_ref = f"exec:{uuid4().hex[:8]}"
    early_done = asyncio.Event()
    activated = asyncio.Event()
    finished = 0

    async def early():
        nonlocal finished
        try:
            decision, _ = await evaluate_pre_submit(
                normal_risk_repo(risk_repo),
                healthy_connection_repo(tenant_id),
                signal_repo,
                recorder,
                tenant_id=tenant_id,
                execution_ref=execution_ref,
                provider_code=PROVIDER,
                symbol=SYMBOL,
                side=SIDE,
                quantity=QTY,
                trace_id=uuid4(),
            )
            return decision
        finally:
            finished += 1
            if finished == _N_EARLY:
                early_done.set()

    async def activator():
        await early_done.wait()
        await risk_repo.insert_safety_control(
            scope=SafetyScope.ACCOUNT,
            scope_ref=str(tenant_id),
            reason="pre-submit-race",
            actor_subject_id=tenant_id,
        )
        activated.set()

    async def late():
        gated = _BarrierGatedRiskRepo(
            risk_repo, cb_level="normal", distrust_level="NORMAL", barrier=activated
        )
        decision, _ = await evaluate_pre_submit(
            gated,
            healthy_connection_repo(tenant_id),
            signal_repo,
            recorder,
            tenant_id=tenant_id,
            execution_ref=execution_ref,
            provider_code=PROVIDER,
            symbol=SYMBOL,
            side=SIDE,
            quantity=QTY,
            trace_id=uuid4(),
        )
        return decision

    results = await asyncio.gather(
        *(early() for _ in range(_N_EARLY)), activator(), *(late() for _ in range(_N_LATE))
    )
    early_results, late_results = results[:_N_EARLY], results[_N_EARLY + 1 :]

    assert all(d.outcome == RiskOutcome.ALLOW for d in early_results)
    assert all(d.outcome == RiskOutcome.DENY for d in late_results)
    assert all("RISK_KILL_SWITCH_ACTIVE_ACCOUNT" in d.reason_codes for d in late_results)
