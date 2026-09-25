"""task-1806 — tick 경로에서 `pre_submit_check.evaluate_submission_gate()`에
넘기는 `mandate_revision_id`/`observed_fence`가 항상 None으로 죽어 있던
결함(P0-B 잔여분) 회귀 테스트.

Spec: 감사 2026-09-06 P0-B(task-1715/1806), R-36(task-1403 a2e2646),
R-33(task-99505ac) `fence_pairs_for`/`read_fence_snapshot` 재사용.
task-2837 DEPTH 감사(task-2721): negative<3·실패주입/성능/게이트적색/다중
인스턴스 증거 없음(D2 미달) — 아래 5건을 추가해 채운다. tick.py 배선
자체는 이미 실값을 관통시키고 있어(src/services/execution_loop/tick.py:249-250)
src 수정은 불필요했다.

`evaluate_submission_gate`(tick.py가 실제로 호출하는 그 함수)를 실제
`make_foundation_pre_submit_gate` 뒤에서 호출해, tick 경로에 배선된 두
"관측-vs-현재" 비교가 죽은 코드가 아님을 증명한다:
1. 관측 fence가 현재보다 1 낮으면(stale) DENY(RISK_FENCE_STALE).
2. 실행이 바인딩된 mandate revision이 amendment로 superseded되면
   DENY(RISK_MANDATE_REVISION_STALE) — fence와 동일한 클래스의 결함이라
   task-1806이 함께 심었다(foundation_gate.py).
3. 둘 다 최신이면 ALLOW.
4. 두 staleness가 동시에 나면 fence 검사가 먼저 걸린다(negative #3).
5. 게이트 적색 재현 — 옛 배선처럼 None을 넘기면 두 staleness 모두 놓친다.
6. 실패 주입 — DB 장애가 fail-open으로 삼켜지지 않고 그대로 전파된다.
7. 성능 단언 — tick 경로 게이트 평가 1회의 p95가 정규화 예산 안에 있다.
8. 다중 인스턴스 — 독립된 두 게이트 인스턴스가 amendment 활성화와 경합해도
   dirty/stale read 없이 early=ALLOW, late=DENY로 갈린다.
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal

import pytest

from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.application.activate_revision import (
    activate_revision as activate_revision_command,
)
from src.foundation.mandates.application.propose_amendment import propose_amendment
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.services.execution_loop.pre_submit_check import evaluate_submission_gate
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.gate import GateOutcome
from tests.adversarial.risk.conftest import fence_reader, seed_execution
from tests.foundation.integration.mandates.conftest import default_rules
from tests.foundation.integration.risk_gate.conftest import activate_mandate_with_defaults
from tests.integration.conftest import create_test_tenant

_SYMBOL = "BTC/USDT"
_SIDE = "BUY"
_QUANTITY = Decimal("0.01")


async def _bind_execution_to_revision(pool, execution_id: int, revision_id) -> None:
    """`strategy_executions.mandate_revision_id`(b3f7e0c1a4d5) — task-1806
    이전엔 이 컬럼을 읽는 호출부가 없었다(tick.py는 항상 None을 넘겼다).
    UI/API가 아직 이 컬럼을 채우는 경로가 없어 직접 UPDATE로 대신한다."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET mandate_revision_id = $1 WHERE id = $2",
            revision_id,
            execution_id,
        )


@pytest.fixture
async def setup(pool):
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id)
    mandate_repo = PostgresMandateRepository(pool)
    trust_repo = PostgresTrustRepository(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
    mandate = await mandate_repo.get_mandate(user_id)
    revision_id = mandate.active_revision_id
    await _bind_execution_to_revision(pool, execution_id, revision_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    read_fence = fence_reader(pool, user_id, execution_id)
    return user_id, execution_id, mandate_repo, revision_id, gate, read_fence


async def test_stale_observed_fence_denies_tick_submission(setup):
    user_id, execution_id, _mandate_repo, revision_id, gate, read_fence = setup
    current = await read_fence()
    stale_key = next(iter(current))
    stale_observed = dict(current)
    stale_observed[stale_key] -= 1  # 현재값보다 1 낮은 관측 F0 — DoD 지정 시나리오

    decision = await evaluate_submission_gate(
        gate,
        user_id=user_id,
        execution_id=execution_id,
        exchange="bitget",
        mandate_revision_id=revision_id,
        observed_fence=stale_observed,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QUANTITY,
    )

    assert decision.outcome == GateOutcome.DENY
    assert decision.reason_codes == ("RISK_FENCE_STALE",)


async def test_superseded_mandate_revision_denies_tick_submission(pool, setup):
    user_id, execution_id, mandate_repo, bound_revision_id, gate, read_fence = setup

    # 실행은 여전히 옛 revision에 바인딩된 채로, mandate는 (non-material)
    # amendment로 새 revision을 activate한다 — 옛 revision은 SUPERSEDED.
    proposed = await propose_amendment(
        mandate_repo, tenant_id=user_id, rules=default_rules(max_total_exposure_pct=50.0)
    )
    trust_repo = PostgresTrustRepository(pool)
    await activate_revision_command(
        mandate_repo,
        trust_repo,
        tenant_id=user_id,
        subject_id=user_id,
        revision_id=proposed.id,
        reauthenticated=False,
    )
    new_revision = await mandate_repo.get_mandate(user_id)
    assert new_revision.active_revision_id != bound_revision_id  # 전제 — 실제로 바뀌었다

    fresh_fence = await read_fence()
    decision = await evaluate_submission_gate(
        gate,
        user_id=user_id,
        execution_id=execution_id,
        exchange="bitget",
        mandate_revision_id=bound_revision_id,  # 실행에 남아 있는 옛(superseded) 값
        observed_fence=fresh_fence,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QUANTITY,
    )

    assert decision.outcome == GateOutcome.DENY
    assert decision.reason_codes == ("RISK_MANDATE_REVISION_STALE",)


async def test_matching_fence_and_mandate_revision_allows_tick_submission(setup):
    user_id, execution_id, _mandate_repo, revision_id, gate, read_fence = setup
    fresh_fence = await read_fence()

    decision = await evaluate_submission_gate(
        gate,
        user_id=user_id,
        execution_id=execution_id,
        exchange="bitget",
        mandate_revision_id=revision_id,
        observed_fence=fresh_fence,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QUANTITY,
    )

    assert decision.outcome == GateOutcome.ALLOW
    assert decision.reason_codes == ()


async def test_stale_fence_denied_even_when_mandate_revision_also_superseded(pool, setup):
    """negative #3 — 두 staleness가 동시에 발생해도 서로를 가리지 않는다.
    `foundation_gate.gate()`는 fence stale/kill-switch를 mandate revision
    대조보다 먼저 평가한다(1층 검사) — 그래서 이 경우 DENY 사유는
    RISK_MANDATE_REVISION_STALE이 아니라 RISK_FENCE_STALE이어야 한다."""
    user_id, execution_id, mandate_repo, bound_revision_id, gate, read_fence = setup

    proposed = await propose_amendment(
        mandate_repo, tenant_id=user_id, rules=default_rules(max_total_exposure_pct=50.0)
    )
    trust_repo = PostgresTrustRepository(pool)
    await activate_revision_command(
        mandate_repo,
        trust_repo,
        tenant_id=user_id,
        subject_id=user_id,
        revision_id=proposed.id,
        reauthenticated=False,
    )

    current = await read_fence()
    stale_key = next(iter(current))
    stale_observed = dict(current)
    stale_observed[stale_key] -= 1

    decision = await evaluate_submission_gate(
        gate,
        user_id=user_id,
        execution_id=execution_id,
        exchange="bitget",
        mandate_revision_id=bound_revision_id,  # 이것도 이미 superseded
        observed_fence=stale_observed,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QUANTITY,
    )

    assert decision.outcome == GateOutcome.DENY
    assert decision.reason_codes == ("RISK_FENCE_STALE",)


async def test_gate_red_when_wiring_omits_superseded_revision_would_allow(pool, setup):
    """게이트 적색 재현 — task-1806 이전 tick.py는 mandate_revision_id·
    observed_fence를 항상 None으로 넘겼다. `test_superseded_mandate_revision_
    denies_tick_submission`과 완전히 동일한 상태(옛 revision에 바인딩된 채로
    amendment가 activate됨)에서 그 옛 배선처럼 None을 넘기면, supersession
    비교 자체가 스킵돼 ALLOW가 나온다 — 위 DENY가 실제로 tick.py가 실값을
    관통시키는 배선에 의존해 갈렸음을 대조군으로 증명한다."""
    user_id, execution_id, mandate_repo, bound_revision_id, gate, _read_fence = setup

    proposed = await propose_amendment(
        mandate_repo, tenant_id=user_id, rules=default_rules(max_total_exposure_pct=50.0)
    )
    trust_repo = PostgresTrustRepository(pool)
    await activate_revision_command(
        mandate_repo,
        trust_repo,
        tenant_id=user_id,
        subject_id=user_id,
        revision_id=proposed.id,
        reauthenticated=False,
    )
    new_revision = await mandate_repo.get_mandate(user_id)
    assert new_revision.active_revision_id != bound_revision_id  # 전제 — 실제로 바뀌었다

    decision = await evaluate_submission_gate(
        gate,
        user_id=user_id,
        execution_id=execution_id,
        exchange="bitget",
        mandate_revision_id=None,  # 옛 배선(P0-B 결함) 재현
        observed_fence=None,  # 옛 배선(P0-B 결함) 재현
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QUANTITY,
    )

    assert decision.outcome == GateOutcome.ALLOW


class _AcquireFailsAfter:
    """실제 pool을 감싸되, `fail_after`번째 이후의 `acquire()` 호출은 DB
    연결 장애를 흉내내 즉시 예외를 던진다 — tick 경로 게이트 평가가 실패를
    삼켜 조용히 ALLOW로 넘어가지 않고 그대로 전파하는지(fail-closed) 실패
    주입으로 증명하는 용도다(tests/integration/test_order_service_risk_gate.py
    관례 재사용)."""

    def __init__(self, real_pool, *, fail_after: int) -> None:
        self._real = real_pool
        self._calls = 0
        self._fail_after = fail_after

    def acquire(self, *args, **kwargs):
        self._calls += 1
        if self._calls > self._fail_after:
            raise ConnectionResetError("simulated DB outage during tick-path gate evaluation")
        return self._real.acquire(*args, **kwargs)


async def test_db_failure_during_tick_gate_evaluation_propagates_instead_of_fail_open(pool, setup):
    user_id, execution_id, _mandate_repo, revision_id, _gate, read_fence = setup
    fresh_fence = await read_fence()
    flaky_pool = _AcquireFailsAfter(pool, fail_after=0)
    flaky_gate = make_foundation_pre_submit_gate(flaky_pool, require_mandate=False)

    with pytest.raises(ConnectionResetError):
        await evaluate_submission_gate(
            flaky_gate,
            user_id=user_id,
            execution_id=execution_id,
            exchange="bitget",
            mandate_revision_id=revision_id,
            observed_fence=fresh_fence,
            symbol=_SYMBOL,
            side=_SIDE,
            quantity=_QUANTITY,
        )


@pytest.mark.perf
async def test_tick_path_gate_latency_within_normalized_budget(pool, setup):
    """D2 수치 성능 단언 — tick 경로 게이트 평가(ALLOW) 1회의 p95 지연을
    같은 연결의 기준 왕복비용(`SELECT 1`)에 정규화한 임계와 비교한다(절대
    ms 상수 회피, tests/integration/test_order_service_risk_gate.py 관례)."""
    user_id, execution_id, _mandate_repo, revision_id, gate, read_fence = setup
    fresh_fence = await read_fence()
    reps = 20

    async def _p95_ms(step) -> float:
        samples = []
        for _ in range(reps):
            t0 = time.perf_counter()
            await step()
            samples.append((time.perf_counter() - t0) * 1000)
        samples.sort()
        return samples[int(len(samples) * 0.95) - 1]

    async with pool.acquire() as conn:
        baseline_p95 = await _p95_ms(lambda: conn.fetchval("SELECT 1"))
    gate_p95 = await _p95_ms(
        lambda: evaluate_submission_gate(
            gate,
            user_id=user_id,
            execution_id=execution_id,
            exchange="bitget",
            mandate_revision_id=revision_id,
            observed_fence=fresh_fence,
            symbol=_SYMBOL,
            side=_SIDE,
            quantity=_QUANTITY,
        )
    )

    # evaluate_submission_gate가 evaluate_pre_submit(200/40x)보다 훨씬 많은
    # 왕복(mandate 해석·fence+control 원자적 읽기·컴플라이언스·mandate 정책
    # 평가·decision WORM 기록)을 순차로 거친다 — 같은 디렉터리의 다른 테스트가
    # 먼저 쌓아 둔 테이블 크기에 따라 관측치가 커질 수 있어 여유를 크게 둔다.
    budget_ms = max(600.0, 120.0 * baseline_p95)
    print(  # noqa: T201 — 실측치는 비차단 기록, 게이트는 아래 assert.
        f"tick pre_submit_check p95={gate_p95:.3f}ms baseline(SELECT 1) p95={baseline_p95:.3f}ms "
        f"budget={budget_ms:.3f}ms"
    )
    assert gate_p95 < budget_ms


_N_EARLY = 3
_N_LATE = 3


async def test_staged_gather_mandate_amendment_vs_concurrent_multi_instance_tick_gate_calls(
    pool, setup
):
    """적대적/다중 인스턴스 증명(D3) — 서로 다른 워커 프로세스를 흉내낸 두
    개의 독립된 게이트 인스턴스(`gate_a`/`gate_b`, 각각
    `make_foundation_pre_submit_gate`를 따로 호출해 만든 별개 클로저)가
    같은 실행에 대해 동시에 tick 경로 게이트를 평가하는 도중 mandate
    amendment가 activate된다(옛 revision이 SUPERSEDED로 바뀐다). early
    그룹(activate가 커밋되기 전에 이미 평가를 마친 호출들)은 여전히 옛
    revision과 일치해 전부 ALLOW, late 그룹(activate 커밋을 기다렸다가
    시작한 호출들)은 옛 revision을 관측값으로 넘겨도 이제 현재값과
    불일치해 전부 DENY(RISK_MANDATE_REVISION_STALE) — dirty read(activate
    전인데 DENY로 새는 경우)도 stale read(activate가 끝났는데도 ALLOW로
    새는 경우)도 없다."""
    user_id, execution_id, mandate_repo, bound_revision_id, _gate, read_fence = setup
    fresh_fence = await read_fence()
    trust_repo = PostgresTrustRepository(pool)

    gate_a = make_foundation_pre_submit_gate(pool, require_mandate=False)
    gate_b = make_foundation_pre_submit_gate(pool, require_mandate=False)

    async def _eval(gate):
        return await evaluate_submission_gate(
            gate,
            user_id=user_id,
            execution_id=execution_id,
            exchange="bitget",
            mandate_revision_id=bound_revision_id,
            observed_fence=fresh_fence,
            symbol=_SYMBOL,
            side=_SIDE,
            quantity=_QUANTITY,
        )

    early_done = asyncio.Event()
    activated = asyncio.Event()
    finished = 0

    async def early(gate):
        nonlocal finished
        try:
            return await _eval(gate)
        finally:
            finished += 1
            if finished == _N_EARLY:
                early_done.set()

    async def activator():
        await early_done.wait()
        proposed = await propose_amendment(
            mandate_repo, tenant_id=user_id, rules=default_rules(max_total_exposure_pct=50.0)
        )
        await activate_revision_command(
            mandate_repo,
            trust_repo,
            tenant_id=user_id,
            subject_id=user_id,
            revision_id=proposed.id,
            reauthenticated=False,
        )
        activated.set()

    async def late(gate):
        await activated.wait()
        return await _eval(gate)

    early_gates = [gate_a, gate_b, gate_a]
    late_gates = [gate_b, gate_a, gate_b]
    assert len(early_gates) == _N_EARLY
    assert len(late_gates) == _N_LATE

    results = await asyncio.gather(
        *(early(g) for g in early_gates), activator(), *(late(g) for g in late_gates)
    )
    early_results = results[:_N_EARLY]
    late_results = results[_N_EARLY + 1 :]

    assert all(d.outcome == GateOutcome.ALLOW for d in early_results)
    assert all(d.outcome == GateOutcome.DENY for d in late_results)
    assert all(d.reason_codes == ("RISK_MANDATE_REVISION_STALE",) for d in late_results)
