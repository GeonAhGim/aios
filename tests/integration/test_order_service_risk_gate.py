"""order_service.submit_order()의 pre_submit_gate 배선 — 전수감사 §6 회귀 +
R-36(mandate 필수 기본값·fence_snapshot 관통).

foundation_gate.make_foundation_pre_submit_gate()가 실제 risk_gate/mandates
DB를 상대로 fence 관통(R-33/R-35 위임)·mandate 필수(I-01 fail-closed)를
올바르게 적용하는지 확인한다. tests/integration/test_order_service.py의
기존 12개 테스트(pre_submit_gate 미지정)는 이 변경으로 전혀 건드리지
않는다(그 경로는 게이트 자체를 안 거친다)."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.application.pause_mandate import pause_mandate
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.application.deactivate_safety_control import (
    deactivate_safety_control,
)
from src.foundation.risk_gate.domain.models import SafetyScope
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.gate import GateOutcome, OrderContext
from src.services.order_service.submit import OrderDeniedByRiskGateError, submit_order
from tests.foundation.integration.risk_gate.conftest import activate_mandate_with_defaults
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def risk_repo(pool):
    return PostgresRiskGateRepository(pool)


@pytest.fixture
def mandate_repo(pool):
    return PostgresMandateRepository(pool)


@pytest.fixture
def trust_repo(pool):
    return PostgresTrustRepository(pool)


async def _create_running_execution(pool: asyncpg.Pool, user_id: uuid.UUID) -> int:
    strategy_id = f"gate-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            json.dumps({}),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
        )
    return row["id"]


def _market_order(execution_id: int) -> Order:
    return Order(
        client_order_id=f"gate-test-{uuid.uuid4().hex}",
        strategy_id="strat-1",
        strategy_version="1.0.0",
        execution_id=execution_id,
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
    )


async def test_active_kill_switch_denies_unmandated_legacy_submit(pool, risk_repo):
    """1층 — mandate가 아예 없어도 kill switch는 legacy 주문을 막는다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    await activate_safety_control(
        risk_repo,
        tenant_id=user_id,
        actor_subject_id=user_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(user_id),
        reason="테스트 — legacy 경로 킬스위치",
    )
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)

    with pytest.raises(OrderDeniedByRiskGateError) as exc_info:
        await submit_order(
            _market_order(execution_id),
            user_id=user_id,
            adapter=FakeExchangeAdapter(),
            pool=pool,
            pre_submit_gate=gate,
        )
    assert any(code.startswith("RISK_KILL_SWITCH_ACTIVE_") for code in exc_info.value.reason_codes)


async def test_unmandated_submit_denied(pool, risk_repo):
    """R-36 — mandate_revision_id가 없으면(기존 실행 전부) 더 이상 통과하지
    않는다(I-01 fail-closed). env var 우회 경로는 제거됐다 — 이 결과는
    조건 없이 항상 적용된다. 거부되기 전에도 감사 기록은 남긴다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)

    with pytest.raises(OrderDeniedByRiskGateError) as exc_info:
        await submit_order(
            _market_order(execution_id),
            user_id=user_id,
            adapter=FakeExchangeAdapter(),
            pool=pool,
            pre_submit_gate=gate,
        )
    assert exc_info.value.reason_codes == ("RISK_MANDATE_REQUIRED",)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM audit_log WHERE action_type = 'risk_gate.unmandated_submit' "
            "AND target_id = $1",
            str(execution_id),
        )
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
    assert row is not None
    assert order_count == 0


async def test_mandated_submit_denied_when_policy_violated(
    pool, risk_repo, mandate_repo, trust_repo
):
    """mandate가 연결된 실행은 정식 정책평가를 거친다 — 정책 위반이면
    DENY(예: 활성 mandate가 PAUSED 상태)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
    await pause_mandate(mandate_repo, tenant_id=user_id)

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    mandate = await mandate_repo.get_mandate(user_id)

    with pytest.raises(OrderDeniedByRiskGateError) as exc_info:
        await submit_order(
            _market_order(execution_id),
            user_id=user_id,
            adapter=FakeExchangeAdapter(),
            pool=pool,
            pre_submit_gate=gate,
            mandate_revision_id=mandate.active_revision_id,
        )
    assert "STATE_MANDATE_PAUSED" in exc_info.value.reason_codes


async def test_mandated_submit_allowed_when_policy_clean(pool, risk_repo, mandate_repo, trust_repo):
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
    mandate = await mandate_repo.get_mandate(user_id)

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    result = await submit_order(
        _market_order(execution_id),
        user_id=user_id,
        adapter=FakeExchangeAdapter(),
        pool=pool,
        pre_submit_gate=gate,
        mandate_revision_id=mandate.active_revision_id,
    )
    assert result.exchange_order_id is not None


async def test_gate_decision_carries_fence_snapshot(pool, risk_repo, mandate_repo, trust_repo):
    """R-33 fence 관통 — ALLOW 결정에도 그 판단의 근거인 F0가 그대로
    실린다(R-37 fenced_submit이 다음 단계에서 재사용할 값)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
    mandate = await mandate_repo.get_mandate(user_id)

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    decision = await gate(
        OrderContext(
            user_id=user_id,
            execution_id=execution_id,
            exchange="bitget",
            mandate_revision_id=mandate.active_revision_id,
        )
    )
    assert decision.fence_snapshot
    assert any(key.startswith("GLOBAL:") for key in decision.fence_snapshot)


async def test_stale_fence_denies_even_when_no_control_is_currently_active(
    pool, risk_repo, mandate_repo, trust_repo
):
    """R-36 negative — 관측(F0) 이후 어떤 scope든 fence token이 증가했다면,
    지금 이 순간 활성 control이 하나도 없어도(비활성화까지 됐어도) DENY —
    단조증가 토큰이라 "이미 무언가 발동한 적 있음" 자체를 stale로 본다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
    mandate = await mandate_repo.get_mandate(user_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)

    context = OrderContext(
        user_id=user_id,
        execution_id=execution_id,
        exchange="bitget",
        mandate_revision_id=mandate.active_revision_id,
    )
    observed = await gate(context)
    assert observed.outcome.value == "ALLOW"

    control = await activate_safety_control(
        risk_repo,
        tenant_id=user_id,
        actor_subject_id=user_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(user_id),
        reason="테스트 — fence stale 유발 후 즉시 해제",
    )
    await deactivate_safety_control(
        risk_repo, tenant_id=user_id, actor_is_admin=True, control_id=control.id
    )

    stale_decision = await gate(
        OrderContext(
            user_id=user_id,
            execution_id=execution_id,
            exchange="bitget",
            mandate_revision_id=mandate.active_revision_id,
            observed_fence=observed.fence_snapshot,
        )
    )
    assert stale_decision.outcome.value == "DENY"
    assert stale_decision.reason_codes == ("RISK_FENCE_STALE",)


async def test_gate_red_without_require_mandate_the_unmandated_submit_would_allow(pool):
    """게이트 적색 재현 — H-1b(task-3369) 이전 기본값이던 `require_mandate=
    False`로 조립하면 mandate 없는 제출이 그냥 통과한다(적색 상태). 위
    `test_unmandated_submit_denied`(같은 시나리오, `require_mandate=True`)가
    실제로 이 배선 값에 의존해 DENY로 갈렸음을 대조군으로 증명한다 — 이
    대조가 없으면 그 DENY가 우연히 다른 경로에서 나왔을 가능성을 배제할 수
    없다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    red_gate = make_foundation_pre_submit_gate(pool, require_mandate=False)

    result = await submit_order(
        _market_order(execution_id),
        user_id=user_id,
        adapter=FakeExchangeAdapter(),
        pool=pool,
        pre_submit_gate=red_gate,
    )
    assert result.exchange_order_id is not None


class _AcquireFailsAfter:
    """실제 pool을 감싸되, `fail_after`번째 이후의 `acquire()` 호출은 DB
    연결 장애를 흉내내 즉시 예외를 던진다. 게이트가 실패를 삼켜 조용히
    ALLOW로 넘어가지 않고 그대로 전파하는지(I-02 fail-closed)를 실패 주입
    으로 증명하는 용도라 `acquire` 이외의 메서드는 위임하지 않는다(게이트가
    이 필드로 만드는 리포지토리들은 전부 `pool.acquire()`만 쓴다)."""

    def __init__(self, real_pool: asyncpg.Pool, *, fail_after: int) -> None:
        self._real = real_pool
        self._calls = 0
        self._fail_after = fail_after

    def acquire(self, *args: object, **kwargs: object) -> object:
        self._calls += 1
        if self._calls > self._fail_after:
            raise ConnectionResetError("simulated DB outage during fence/control read")
        return self._real.acquire(*args, **kwargs)


async def test_db_failure_during_fence_read_propagates_instead_of_fail_open(
    pool, mandate_repo, trust_repo
):
    """실패 주입 — fence/control 조회 중 DB 연결이 끊기면 예외가 그대로
    전파돼야 한다(fail-open으로 조용히 ALLOW를 내주면 안 된다). 주문도
    생성되지 않아야 한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
    mandate = await mandate_repo.get_mandate(user_id)

    flaky_pool = _AcquireFailsAfter(pool, fail_after=0)
    gate = make_foundation_pre_submit_gate(flaky_pool, require_mandate=True)  # type: ignore[arg-type]

    with pytest.raises(ConnectionResetError):
        await gate(
            OrderContext(
                user_id=user_id,
                execution_id=execution_id,
                exchange="bitget",
                mandate_revision_id=mandate.active_revision_id,
            )
        )

    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
    assert order_count == 0


@pytest.mark.perf
async def test_gate_latency_within_normalized_budget(pool, mandate_repo, trust_repo):
    """D2 수치 성능 단언 — ALLOW 판정 1회의 p95 지연을 같은 연결의 기준
    왕복비용(`SELECT 1`)에 정규화한 임계와 비교한다. 절대 ms 상수는 공유 CI
    환경에서 로컬 대비 크게 흔들려 상시 적색을 낳은 전례가 있다
    (tests/integration/oms/test_gate_perf_multiinstance.py와 동일 관례)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
    mandate = await mandate_repo.get_mandate(user_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    context = OrderContext(
        user_id=user_id,
        execution_id=execution_id,
        exchange="bitget",
        mandate_revision_id=mandate.active_revision_id,
    )
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
    gate_p95 = await _p95_ms(lambda: gate(context))

    budget_ms = max(200.0, 40.0 * baseline_p95)
    print(  # noqa: T201 — 실측치는 비차단 기록, 게이트는 아래 assert.
        f"foundation_gate p95={gate_p95:.3f}ms baseline(SELECT 1) p95={baseline_p95:.3f}ms "
        f"budget={budget_ms:.3f}ms"
    )
    assert gate_p95 < budget_ms


_N_EARLY = 3
_N_LATE = 3


async def test_staged_gather_kill_switch_vs_concurrent_multi_instance_gate_calls(
    pool, risk_repo, mandate_repo, trust_repo
):
    """적대적/다중 인스턴스 증명(D3) — 서로 다른 워커 프로세스를 흉내낸
    두 개의 독립된 게이트 인스턴스(`gate_a`/`gate_b`, 각각
    `make_foundation_pre_submit_gate`를 따로 호출해 만든 별개 클로저)가
    같은 tenant에 대해 동시에 제출을 평가하는 도중 kill switch가 활성화
    된다. early 그룹(활성화가 커밋되기 전에 이미 평가를 마친 호출들)은
    전부 ALLOW, late 그룹(활성화 커밋을 기다렸다가 시작한 호출들)은 전부
    DENY(RISK_KILL_SWITCH_ACTIVE_*) — dirty read(활성화 전인데 DENY로
    새는 경우)도 stale read(활성화가 끝났는데도 ALLOW로 새는 경우)도
    없다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
    mandate = await mandate_repo.get_mandate(user_id)

    gate_a = make_foundation_pre_submit_gate(pool, require_mandate=True)
    gate_b = make_foundation_pre_submit_gate(pool, require_mandate=True)

    def _ctx() -> OrderContext:
        return OrderContext(
            user_id=user_id,
            execution_id=execution_id,
            exchange="bitget",
            mandate_revision_id=mandate.active_revision_id,
        )

    early_done = asyncio.Event()
    activated = asyncio.Event()
    finished = 0

    async def early(gate):
        nonlocal finished
        try:
            return await gate(_ctx())
        finally:
            finished += 1
            if finished == _N_EARLY:
                early_done.set()

    async def activator():
        await early_done.wait()
        await activate_safety_control(
            risk_repo,
            tenant_id=user_id,
            actor_subject_id=user_id,
            actor_is_admin=False,
            scope=SafetyScope.ACCOUNT,
            scope_ref=str(user_id),
            reason="다중 인스턴스 경합 테스트",
        )
        activated.set()

    async def late(gate):
        await activated.wait()
        return await gate(_ctx())

    early_gates = [gate_a, gate_b, gate_a]
    late_gates = [gate_b, gate_a, gate_b]
    assert len(early_gates) == _N_EARLY
    assert len(late_gates) == _N_LATE

    results = await asyncio.gather(
        *(early(g) for g in early_gates), activator(), *(late(g) for g in late_gates)
    )
    early_results, late_results = results[:_N_EARLY], results[_N_EARLY + 1 :]

    assert all(d.outcome == GateOutcome.ALLOW for d in early_results)
    assert all(d.outcome == GateOutcome.DENY for d in late_results)
    assert all(
        any(code.startswith("RISK_KILL_SWITCH_ACTIVE_") for code in d.reason_codes)
        for d in late_results
    )
