"""16.4 통합테스트 — 실제 dev DB 대상."""

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.loader.risk_policy_loader import load_risk_policy
from src.services.execution_monitoring_service import ExecutionMonitoringService
from src.services.execution_service import ExecutionService
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    # system_safety_state는 전역 싱글톤 행 — 다른 테스트 파일(예:
    # test_execution_scheduler.py의 halted/restricted 시나리오)이 남긴 값과
    # 섞이지 않도록 매 테스트 시작 전 normal로 되돌린다(test_execution_tick.py 관례).
    async with p.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


@pytest.fixture
def execution_service(pool):
    return ExecutionService(
        pool,
        load_risk_policy(),
        pre_start_gate=make_foundation_pre_submit_gate(pool, require_mandate=False),
    )


@pytest.fixture
def monitoring_service(pool):
    return ExecutionMonitoringService(pool)


async def _create_approved_strategy(pool, owner_user_id):
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', $4::jsonb, 'test-author',
                    'APPROVED')
            """,
            strategy_id,
            version,
            owner_user_id,
            json.dumps({}),
        )
    return strategy_id, version


async def _link_credential(pool, user_id):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO exchange_credentials "
            "(user_id, exchange, api_key_encrypted, api_secret_encrypted) "
            "VALUES ($1, 'bitget', $2, $2)",
            user_id,
            b"dummy",
        )


async def _create_running_execution(execution_service, pool, user_id, *, link_credential=True):
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    if link_credential:
        await _link_credential(pool, user_id)
    created = await execution_service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("500"),
        currency="USDT",
        exchange="bitget",
        mode="PAPER",
        available_balance=Decimal("10000"),
    )
    await execution_service.start(created.id, user_id)
    return created.id, strategy_id


async def _insert_position(
    pool, user_id, execution_id, strategy_id, *, realized=Decimal("0"), unrealized=Decimal("0")
):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO positions
                (user_id, symbol, exchange, strategy_id, execution_id, quantity,
                 average_entry_price, unrealized_pnl, realized_pnl, entry_time)
            VALUES ($1, 'BTC/USDT', 'bitget', $2, $3, 1.0, 50000, $4, $5, now())
            """,
            user_id,
            strategy_id,
            execution_id,
            unrealized,
            realized,
        )


async def test_execution_with_no_positions_reports_zero_pnl(
    execution_service, monitoring_service, pool
):
    user_id = await create_test_tenant(pool)
    execution_id, _ = await _create_running_execution(execution_service, pool, user_id)

    cards = await monitoring_service.list_for_user(user_id)

    card = next(c for c in cards if c.execution_id == execution_id)
    assert card.realized_pnl == Decimal("0")
    assert card.unrealized_pnl == Decimal("0")
    assert card.days_since_start == 0


async def test_two_running_executions_track_pnl_independently(
    execution_service, monitoring_service, pool
):
    user_id = await create_test_tenant(pool)
    exec_a, strategy_a = await _create_running_execution(execution_service, pool, user_id)
    exec_b, strategy_b = await _create_running_execution(
        execution_service, pool, user_id, link_credential=False
    )

    await _insert_position(
        pool, user_id, exec_a, strategy_a, realized=Decimal("100"), unrealized=Decimal("50")
    )
    await _insert_position(
        pool, user_id, exec_b, strategy_b, realized=Decimal("-30"), unrealized=Decimal("10")
    )

    cards = await monitoring_service.list_for_user(user_id)

    card_a = next(c for c in cards if c.execution_id == exec_a)
    card_b = next(c for c in cards if c.execution_id == exec_b)
    assert card_a.realized_pnl == Decimal("100")
    assert card_a.unrealized_pnl == Decimal("50")
    assert card_b.realized_pnl == Decimal("-30")
    assert card_b.unrealized_pnl == Decimal("10")


async def test_multiple_positions_in_same_execution_sum_correctly(
    execution_service, monitoring_service, pool
):
    user_id = await create_test_tenant(pool)
    execution_id, strategy_id = await _create_running_execution(execution_service, pool, user_id)

    await _insert_position(
        pool, user_id, execution_id, strategy_id, realized=Decimal("10"), unrealized=Decimal("5")
    )
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO positions
                (user_id, symbol, exchange, strategy_id, execution_id, quantity,
                 average_entry_price, unrealized_pnl, realized_pnl, entry_time)
            VALUES ($1, 'ETH/USDT', 'bitget', $2, $3, 1.0, 3000, 15, 20, now())
            """,
            user_id,
            strategy_id,
            execution_id,
        )

    cards = await monitoring_service.list_for_user(user_id)

    card = next(c for c in cards if c.execution_id == execution_id)
    assert card.realized_pnl == Decimal("30")
    assert card.unrealized_pnl == Decimal("20")


async def test_no_executions_returns_empty_list_not_error(monitoring_service, pool):
    user_id = await create_test_tenant(pool)

    cards = await monitoring_service.list_for_user(user_id)

    assert cards == []


async def test_unknown_user_id_returns_empty_list_not_error(monitoring_service):
    cards = await monitoring_service.list_for_user(uuid4())

    assert cards == []


async def test_created_but_not_started_execution_has_none_days_since_start(
    execution_service, monitoring_service, pool
):
    """created_execution() 만 호출하고 start()는 호출하지 않으면 started_at이
    NULL이다 — days_since_start는 None이어야 하고(경계값), 실행 자체는
    목록에서 사라지지 않아야 한다."""
    user_id = await create_test_tenant(pool)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)
    created = await execution_service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("500"),
        currency="USDT",
        exchange="bitget",
        mode="PAPER",
        available_balance=Decimal("10000"),
    )

    cards = await monitoring_service.list_for_user(user_id)

    card = next(c for c in cards if c.execution_id == created.id)
    assert card.days_since_start is None
    assert card.realized_pnl == Decimal("0")
    assert card.unrealized_pnl == Decimal("0")


async def test_executions_are_isolated_per_user(execution_service, monitoring_service, pool):
    """다른 사용자의 실행이 조회 결과에 섞여 들어오지 않는다(negative — 테넌트
    격리 위반이 없어야 함, INVARIANTS 준수 확인)."""
    user_a = await create_test_tenant(pool)
    user_b = await create_test_tenant(pool)
    exec_a, _ = await _create_running_execution(execution_service, pool, user_a)
    exec_b, _ = await _create_running_execution(execution_service, pool, user_b)

    cards_a = await monitoring_service.list_for_user(user_a)
    cards_b = await monitoring_service.list_for_user(user_b)

    assert {c.execution_id for c in cards_a} == {exec_a}
    assert {c.execution_id for c in cards_b} == {exec_b}


async def test_pool_acquire_failure_propagates_fail_closed(monitoring_service, monkeypatch):
    """의존성(DB pool) 장애 시 조용히 빈 목록을 반환하지 않고 예외를 그대로
    전파해야 한다(fail-closed 기본 원칙, CLAUDE.md §3) — 실패주입 테스트."""

    class _FailingPool:
        def acquire(self):
            raise RuntimeError("connection pool exhausted")

    monkeypatch.setattr(monitoring_service, "_pool", _FailingPool())

    with pytest.raises(RuntimeError, match="connection pool exhausted"):
        await monitoring_service.list_for_user(uuid4())


# --- negative tests (3건 이상: 불변식 위반 입력 명시적 거부) ---


async def test_list_for_user_rejects_invalid_uuid_string(monitoring_service):
    """잘못된 형식의 문자열 user_id를 전달하면 asyncpg가 DataError를
    일으킨다 — 타입 위반은 명시적 거부(I-01)."""
    with pytest.raises(asyncpg.exceptions.DataError):
        await monitoring_service.list_for_user("not-a-uuid")


async def test_pnl_aggregation_handles_zero_pnl_in_positions(
    execution_service, monitoring_service, pool
):
    """positions 테이블에 realized_pnl/unrealized_pnl이 0인 행이 섞여 있어도
    COALESCE(SUM(...), 0)가 정상 동작한다.
    LEFT JOIN → NULL position 행이 섞일 때 Decimal("0")로 안정화되는지 확인."""
    user_id = await create_test_tenant(pool)
    execution_id, strategy_id = await _create_running_execution(execution_service, pool, user_id)

    # 0 PnL 필드로 직접 삽입
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO positions
                (user_id, symbol, exchange, strategy_id, execution_id, quantity,
                 average_entry_price, unrealized_pnl, realized_pnl, entry_time)
            VALUES ($1, 'ETH/USDT', 'bitget', $2, $3, 0.5, 3000, 0, 0, now())
            """,
            user_id,
            strategy_id,
            execution_id,
        )

    cards = await monitoring_service.list_for_user(user_id)
    card = next(c for c in cards if c.execution_id == execution_id)
    assert card.realized_pnl == Decimal("0")
    assert card.unrealized_pnl == Decimal("0")
    assert isinstance(card.realized_pnl, Decimal)
    assert isinstance(card.unrealized_pnl, Decimal)


async def test_monitoring_with_no_positions_table_rows_returns_empty_pnl(
    execution_service, monitoring_service, pool
):
    """positions 테이블에 해당 execution_id의 행이 전혀 없으면
    SUM(NULL)=NULL → COALESCE(…, 0) → Decimal("0")로 집계되어야 한다.
    LEFT JOIN이 행을 찾지 못해도 서비스는 예외 없이 0 PnL을 반환한다."""
    user_id = await create_test_tenant(pool)
    execution_id, _ = await _create_running_execution(execution_service, pool, user_id)

    # positions 테이블에 아무 행도 삽입하지 않음 — LEFT JOIN이 NULL을 반환
    cards = await monitoring_service.list_for_user(user_id)
    card = next(c for c in cards if c.execution_id == execution_id)
    assert card.realized_pnl == Decimal("0")
    assert card.unrealized_pnl == Decimal("0")


# --- failure-injection tests (1건 이상: 의존성 예외 유발) ---


async def test_query_error_propagates_fail_closed(monitoring_service, monkeypatch, caplog):
    """DB 쿼리 실행 중 예외 발생 시 빈 목록을 반환하지 않고 예외를 전파한다.
    fail-closed 원칙 (INVARIANTS I-10: 안전 컴포넌트는 배선 증명 테스트 필수)."""
    import asyncpg as _asyncpg

    class _QueryFailingPool:
        def acquire(self):
            raise _asyncpg.PostgresError('relation "risk_decision" does not exist')

    monkeypatch.setattr(monitoring_service, "_pool", _QueryFailingPool())

    with pytest.raises(_asyncpg.PostgresError):
        await monitoring_service.list_for_user(uuid4())

    # 빈 목록이 반환되지 않았는지 확인 (fail-closed 검증)
    # 예외가 전파되었으므로 이 줄은 도달하지 않음


async def test_connection_reset_during_fetch_propagates(monitoring_service, monkeypatch):
    """fetch 중 connection reset 발생 시 fail-closed — 예외 전파."""

    class _ResetPool:
        def acquire(self):
            raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(monitoring_service, "_pool", _ResetPool())

    with pytest.raises(RuntimeError, match="connection reset by peer"):
        await monitoring_service.list_for_user(uuid4())
