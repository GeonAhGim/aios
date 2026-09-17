"""FD-8 실행 루프 스케줄러 통합테스트 — RUNNING PAPER 실행 전부를 tick.

전수감사(docs/FULL_AUDIT_2026-09-02.md §3) 회귀: run_execution_tick은 완전했지만
운영 앱에서 호출되지 않았다. 이 테스트는 스케줄러가 실제 DB의 RUNNING 실행을
찾아 tick하고, 한 실행의 실패가 나머지를 막지 않으며, LIVE·비RUNNING 실행은
건드리지 않음을 실제 Postgres 위에서 검증한다.

공유 dev DB에는 다른 테스트가 남긴 RUNNING 실행이 있을 수 있다 — 어댑터
리졸버가 이 테스트의 사용자만 알고 나머지는 CredentialNotFoundError를 내므로
그 실행들은 "자격증명 없음"으로 건너뛰어진다. 따라서 단언은 "내 실행 ⊆ 결과"
형태로만 쓴다.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.safety.data_distrust import DataDistrustMonitor
from src.data.models.market_data import Ticker
from src.data.models.trading import AccountBalance, OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.execution_ownership.adapters.postgres_repository import (
    PostgresExecutionLeaseRepository,
)
from src.services.credential_resolver import CredentialNotFoundError
from src.services.execution_loop.scheduler import ExecutionLoopScheduler
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from tests.integration.conftest import create_test_tenant, create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.test_execution_tick import _create_execution


def _asyncpg_dsn() -> str:
    # tests/conftest.py가 TEST_DATABASE_URL을 DATABASE_URL 환경변수로 옮겨
    # 두므로(이 worktree 전용 DB), 다른 통합테스트(execution_ownership/conftest.py
    # 등)와 동일하게 os.environ에서 읽는다 — .env 파일을 직접 파싱하면 이
    # override를 우회해 공유 dev DB(migrations 미적용 가능)로 잘못 붙는다.
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _allow_all(_context: OrderContext) -> GateDecision:
    return GateDecision(
        outcome=GateOutcome.ALLOW, decision_id=uuid.uuid4(), compliance_decision_id=uuid.uuid4()
    )


def _owner_id() -> str:
    # execution_leases는 이 테스트 세션 전체가 공유하는 테이블이라(트랜잭션
    # 롤백 격리 없음) 매 테스트마다 고유한 owner_id를 써야 다른 테스트가
    # 남긴 리스와 섞이지 않는다(test_postgres_lease_repository.py와 동일 이유).
    return f"scheduler-test-owner-{uuid.uuid4().hex[:8]}"


def _scheduler(pool: asyncpg.Pool, **overrides: object) -> ExecutionLoopScheduler:
    kwargs: dict[str, object] = dict(
        resolve_adapter=_resolver_for({}),
        policy=load_risk_policy(),
        pre_submit_gate=_allow_all,
        distrust_monitor=DataDistrustMonitor(),
        lease_repo=PostgresExecutionLeaseRepository(pool),
        owner_id=_owner_id(),
    )
    kwargs.update(overrides)
    return ExecutionLoopScheduler(pool, **kwargs)  # type: ignore[arg-type]


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    async with p.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


def _filled_adapter() -> FakeExchangeAdapter:
    return FakeExchangeAdapter(
        closes=[Decimal("50")] * 65,
        place_order_result_status=OrderStatus.FILLED,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )


def _resolver_for(adapters: dict[uuid.UUID, ExchangeAdapter]):
    async def resolve(user_id: uuid.UUID, exchange: str) -> ExchangeAdapter:
        try:
            return adapters[user_id]
        except KeyError as exc:
            raise CredentialNotFoundError(f"{exchange} 자격증명 없음(테스트 리졸버)") from exc

    return resolve


async def _fsm_state(pool: asyncpg.Pool, execution_id: int) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )


async def test_tick_all_running_ticks_every_paper_execution(pool):
    # risk_decision.tenant_id는 tenant(id)를 FK한다(94da854f522f, task-1988) —
    # 이 틱이 위험 단계까지 도달해 risk_decision을 쓰므로 create_test_user만으로는
    # 대응하는 tenant 행이 없어 FK 위반이 난다.
    user_a = await create_test_tenant(pool)
    user_b = await create_test_tenant(pool)
    exec_a = await _create_execution(pool, user_a, entry_threshold=100.0)
    exec_b = await _create_execution(pool, user_b, entry_threshold=100.0)
    adapter_a, adapter_b = _filled_adapter(), _filled_adapter()
    scheduler = _scheduler(
        pool, resolve_adapter=_resolver_for({user_a: adapter_a, user_b: adapter_b})
    )

    report = await scheduler.tick_all_running()

    assert {exec_a, exec_b} <= set(report.ticked)
    assert not ({exec_a, exec_b} & set(report.failed))
    assert adapter_a.place_order_call_count == 1
    assert adapter_b.place_order_call_count == 1
    assert await _fsm_state(pool, exec_a) == "HOLDING"
    assert await _fsm_state(pool, exec_b) == "HOLDING"


async def test_one_failing_execution_does_not_block_the_others(pool):
    # risk_decision.tenant_id FK가 tenant(id)를 가리킨다(94da854f522f) — 두
    # 실행 모두 위험 단계에 도달하므로 tenant 행이 있는 사용자가 필요하다.
    user_ok = await create_test_tenant(pool)
    user_broken = await create_test_tenant(pool)
    exec_ok = await _create_execution(pool, user_ok, entry_threshold=100.0)
    exec_broken = await _create_execution(pool, user_broken, entry_threshold=100.0)

    class ExplodingAdapter(FakeExchangeAdapter):
        async def get_ohlcv(self, symbol, timeframe, limit=100):
            raise RuntimeError("거래소 장애(테스트)")

    scheduler = _scheduler(
        pool,
        resolve_adapter=_resolver_for(
            {user_ok: _filled_adapter(), user_broken: ExplodingAdapter()}
        ),
    )

    report = await scheduler.tick_all_running()

    assert exec_ok in report.ticked
    assert exec_broken in report.failed
    assert "RuntimeError" in report.failed[exec_broken]
    assert await _fsm_state(pool, exec_ok) == "HOLDING"
    assert await _fsm_state(pool, exec_broken) == "IDLE"


async def test_missing_credential_is_skipped_not_failed(pool):
    user = await create_test_user(pool)
    execution_id = await _create_execution(pool, user, entry_threshold=100.0)
    scheduler = _scheduler(pool)

    report = await scheduler.tick_all_running()

    assert execution_id in report.skipped_no_credential
    assert execution_id not in report.ticked
    assert execution_id not in report.failed


async def test_live_and_paused_executions_are_never_ticked(pool):
    user = await create_test_user(pool)
    live_id = await _create_execution(pool, user, entry_threshold=100.0)
    paused_id = await _create_execution(pool, user, entry_threshold=100.0)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE strategy_executions SET mode = 'LIVE' WHERE id = $1", live_id)
        await conn.execute(
            "UPDATE strategy_executions SET status = 'PAUSED', paused_by = 'USER' WHERE id = $1",
            paused_id,
        )
    adapter = _filled_adapter()
    scheduler = _scheduler(pool, resolve_adapter=_resolver_for({user: adapter}))

    candidate_ids = {row["id"] for row in await scheduler.list_candidates()}
    report = await scheduler.tick_all_running()

    assert live_id not in candidate_ids
    assert paused_id not in candidate_ids
    assert live_id not in report.ticked and paused_id not in report.ticked
    assert adapter.place_order_call_count == 0


async def test_execution_with_lease_held_by_other_owner_is_skipped(pool):
    """§4.1 — 다른 프로세스가 만료 전 리스를 쥔 execution_id는 RUNNING/PAPER라도
    이번 주기 tick 대상에서 빠진다(예외 없이 건너뜀). 같은 주기에 리스가 없는
    다른 execution은 정상적으로 tick된다."""
    # risk_decision.tenant_id FK가 tenant(id)를 가리킨다(94da854f522f) — free_id는
    # 위험 단계까지 도달해 risk_decision을 쓰므로 tenant 행이 있는 사용자가 필요하다.
    user_leased, user_free = await create_test_tenant(pool), await create_test_tenant(pool)
    leased_id = await _create_execution(pool, user_leased, entry_threshold=100.0)
    free_id = await _create_execution(pool, user_free, entry_threshold=100.0)
    lease_repo = PostgresExecutionLeaseRepository(pool)
    await lease_repo.acquire_or_renew_many(
        [leased_id], owner_id="other-process-owner", ttl_seconds=60
    )
    adapter_leased, adapter_free = _filled_adapter(), _filled_adapter()
    scheduler = _scheduler(
        pool,
        resolve_adapter=_resolver_for({user_leased: adapter_leased, user_free: adapter_free}),
        lease_repo=lease_repo,
    )

    candidate_ids = {row["id"] for row in await scheduler.list_candidates()}
    report = await scheduler.tick_all_running()

    assert leased_id not in candidate_ids
    assert free_id in candidate_ids
    assert leased_id not in report.ticked
    assert leased_id not in report.failed
    assert free_id in report.ticked
    assert adapter_leased.place_order_call_count == 0
    assert adapter_free.place_order_call_count == 1


def _reference_ticker(price: str) -> Ticker:
    return Ticker(
        symbol="BTC/USDT",
        exchange="reference",
        price=Decimal(price),
        bid=Decimal(price),
        ask=Decimal(price),
        volume_24h=Decimal("1"),
        timestamp=datetime.now(timezone.utc),
        source_type="reference",
    )


class _StubReferenceProvider:
    def __init__(self, price: str) -> None:
        self._price = price

    async def get_reference_ticker(self, symbol: str) -> Ticker | None:
        return _reference_ticker(self._price)


async def test_distrust_provider_factory_wiring_blocks_order_on_diverging_references(pool):
    """게이트 적색 재현 + 배선 결함 회귀(task-2810) — 예전엔
    `ExecutionLoopScheduler`가 `distrust_providers`를 항상 기본값 `()`으로
    넘겨(`distrust_provider_factory` 자체가 없었음) 참조 쿼럼 비교가
    프로덕션에서 한 번도 실행되지 않았다. 여기서는 monkeypatch 없이 실제
    `check_and_persist_distrust`/`DataDistrustMonitor.check()` 경로를 그대로
    타고, 공격적으로 벌어진(primary=50 vs 참조 150/151) 참조 시세 2개를
    주입해 실제 DISTRUSTED 판정 -> 신규 주문 차단까지 end-to-end로 증명한다."""
    user = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user, entry_threshold=100.0)
    adapter = _filled_adapter()  # closes=[50]*65 -> primary ticker price=50

    def _diverging_factory(_adapter: object, _exchange: str) -> list[_StubReferenceProvider]:
        return [_StubReferenceProvider("150"), _StubReferenceProvider("151")]

    scheduler = _scheduler(
        pool,
        resolve_adapter=_resolver_for({user: adapter}),
        distrust_provider_factory=_diverging_factory,
    )

    report = await scheduler.tick_all_running()

    assert execution_id in report.ticked  # tick 자체는 실패가 아니다(신규 주문만 스킵)
    assert adapter.place_order_call_count == 0
    assert await _fsm_state(pool, execution_id) == "IDLE"
    async with pool.acquire() as conn:
        level = await conn.fetchval(
            "SELECT level FROM data_distrust_state WHERE exchange = 'bitget' AND symbol = "
            "(SELECT target_asset FROM strategies s JOIN strategy_executions e "
            " ON e.strategy_id = s.strategy_id AND e.strategy_version = s.version "
            " WHERE e.id = $1)",
            execution_id,
        )
    assert level == "DISTRUSTED"


async def test_circuit_breaker_halted_blocks_new_orders(pool):
    """negative — system_safety_state.circuit_breaker_level='halted'인 동안은
    safety_state 규칙(src/core/risk/rules/safety_state.py R-13,
    docs/design/INVARIANTS.md I-09 두 독립 권위 중 RiskEngine 축)이 신규 주문을
    명시적으로 거부한다. tick 자체는 실패로 세지 않는다(신규 주문만 스킵,
    RISK_CIRCUIT_BREAKER_HALTED — DENY)."""
    user = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user, entry_threshold=100.0)
    adapter = _filled_adapter()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'halted' WHERE id = 1"
        )
    scheduler = _scheduler(pool, resolve_adapter=_resolver_for({user: adapter}))

    report = await scheduler.tick_all_running()

    assert execution_id in report.ticked
    assert execution_id not in report.failed
    assert adapter.place_order_call_count == 0
    assert await _fsm_state(pool, execution_id) == "IDLE"


async def test_circuit_breaker_restricted_blocks_new_orders(pool):
    """negative — `_CB_DENY_LEVELS`의 다른 값('restricted')도 독립적으로
    재현한다. 'halted' 분기만 통과하고 다른 deny 레벨의 분기가 깨져도 그
    회귀는 위 테스트 하나만으로는 드러나지 않는다."""
    user = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user, entry_threshold=100.0)
    adapter = _filled_adapter()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'restricted' WHERE id = 1"
        )
    scheduler = _scheduler(pool, resolve_adapter=_resolver_for({user: adapter}))

    report = await scheduler.tick_all_running()

    assert execution_id in report.ticked
    assert execution_id not in report.failed
    assert adapter.place_order_call_count == 0
    assert await _fsm_state(pool, execution_id) == "IDLE"


async def test_execution_paused_by_safety_layer_blocks_new_orders_but_keeps_ticking(pool):
    """negative — status='RUNNING'인 채 paused_by='SAFETY_LAYER'만 서 있는 실행은
    (test_live_and_paused_executions_are_never_ticked의 USER 일시정지 케이스와
    달리 status 자체는 그대로다, 8e22a459e6ab ZuluGuard 자동정지 설계) 여전히
    tick 후보지만 safety_state 규칙(RISK_EXECUTION_PAUSED_BY_SAFETY)이 신규
    주문을 거부해야 한다."""
    user = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user, entry_threshold=100.0)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET paused_by = 'SAFETY_LAYER' WHERE id = $1",
            execution_id,
        )
    adapter = _filled_adapter()
    scheduler = _scheduler(pool, resolve_adapter=_resolver_for({user: adapter}))

    candidate_ids = {row["id"] for row in await scheduler.list_candidates()}
    report = await scheduler.tick_all_running()

    assert execution_id in candidate_ids
    assert execution_id in report.ticked
    assert adapter.place_order_call_count == 0
    assert await _fsm_state(pool, execution_id) == "IDLE"


async def test_lease_repo_failure_during_list_candidates_is_not_swallowed(pool):
    """실패주입 — list_candidates 내부에서 리스 저장소가 예외를 던지면
    tick_all_running이 그 예외를 삼키지 않고 그대로 전파해야 한다.
    `_tick_one`의 try/except는 개별 실행의 tick 실패만 격리하는 용도이며,
    list_candidates 단계(리스 획득/갱신)의 장애까지 삼켜 "이번 주기 전부
    조용히 스킵"으로 둔갑시키면 안 된다(CLAUDE.md #3 fail-closed 기본 원칙 —
    run_forever만 최상위에서 이 예외를 캐치한다, scheduler.py L200-207)."""
    user = await create_test_tenant(pool)
    await _create_execution(pool, user, entry_threshold=100.0)

    class ExplodingLeaseRepo(PostgresExecutionLeaseRepository):
        async def acquire_or_renew_many(
            self, execution_ids: list[int], *, owner_id: str, ttl_seconds: float
        ) -> set[int]:
            raise RuntimeError("lease store unavailable(테스트 주입)")

    scheduler = _scheduler(pool, lease_repo=ExplodingLeaseRepo(pool))

    with pytest.raises(RuntimeError, match="lease store unavailable"):
        await scheduler.tick_all_running()


async def test_tick_all_running_p95_latency_within_budget(pool):
    """성능 단언(D2, ADR-2026-09-09-C Decision 1) — 이 스케줄러 축은 예산표에
    전용 행이 없다(가장 가까운 행은 "주문 제출→ACK p95 50ms(paper)"이지만
    tick_all_running은 전략/포트폴리오/리스크/실행 4엔진 + 리스 획득 + DB
    왕복을 한 번에 묶으므로 그보다 넉넉해야 한다). 회귀 감지용으로 2개 실행을
    동시에 tick하는 데 2초를 예산으로 건다."""
    durations: list[float] = []
    for _ in range(5):
        user_a, user_b = await create_test_tenant(pool), await create_test_tenant(pool)
        exec_a = await _create_execution(pool, user_a, entry_threshold=100.0)
        exec_b = await _create_execution(pool, user_b, entry_threshold=100.0)
        scheduler = _scheduler(
            pool,
            resolve_adapter=_resolver_for({user_a: _filled_adapter(), user_b: _filled_adapter()}),
        )

        start = time.perf_counter()
        report = await scheduler.tick_all_running()
        durations.append(time.perf_counter() - start)

        assert {exec_a, exec_b} <= set(report.ticked)

    durations.sort()
    p95 = durations[-1]
    assert p95 < 2.0, f"tick_all_running p95 latency {p95:.3f}s exceeded 2.0s budget"


def test_interval_comes_from_risk_policy():
    policy = load_risk_policy()
    scheduler = _scheduler(None, policy=policy)  # type: ignore[arg-type]
    assert scheduler.interval_seconds == policy.execution_loop.interval_sec
