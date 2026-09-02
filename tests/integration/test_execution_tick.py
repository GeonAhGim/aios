"""FD-8.1~8.4 실행 루프 통합테스트 — StrategyEngine→PortfolioEngine→
RiskEngine→Executor 전체 파이프라인 왕복(거래소는 FakeExchangeAdapter 대역).
"""
import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.executor.executor import Executor
from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.portfolio.engine import PortfolioEngine
from src.core.risk.engine import RiskEngine
from src.core.strategy.engine import StrategyEngine
from src.data.models.strategy_fsm import FSMState
from src.data.models.trading import AccountBalance, OrderStatus
from src.services.condition_compiler import ConditionCompiler
from src.services.execution_loop.equity_tracker import ExecutionEquityTracker
from src.services.execution_loop.tick import _make_fsm_state_writer, run_execution_tick
from src.services.preview_service import PreviewCondition
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    async with p.acquire() as conn:
        # system_safety_state는 전역 싱글톤 행이라 다른 테스트 파일(예:
        # test_circuit_breaker.py)이 남긴 상태에 오염될 수 있다 — 그 파일의
        # 격리 관례를 그대로 따른다.
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


def _fsm_definition(*, entry_threshold: float) -> dict:
    compiled = ConditionCompiler().compile(
        strategy_id="tick-test",
        version="1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        author_agent="test",
        entry_conditions=[
            PreviewCondition(
                indicator="SMA", params={"timeperiod": 5}, operator="<", threshold=entry_threshold
            )
        ],
        exit_conditions=[
            PreviewCondition(
                indicator="SMA", params={"timeperiod": 5}, operator=">", threshold=999999.0
            )
        ],
        stop_loss_conditions=[
            PreviewCondition(
                indicator="SMA", params={"timeperiod": 5}, operator="<", threshold=0.0
            )
        ],
    )
    return json.loads(compiled.model_dump_json())


async def _create_execution(
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
    *,
    entry_threshold: float = 100.0,
    allocated_capital: Decimal = Decimal("1000"),
) -> int:
    strategy_id = f"tick-test-{uuid.uuid4().hex[:8]}"
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
            json.dumps(_fsm_definition(entry_threshold=entry_threshold)),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', $3, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
            allocated_capital,
        )
    return row["id"]


def _engines():
    return {
        "strategy_engine": StrategyEngine(),
        "portfolio_engine": PortfolioEngine(),
        "risk_engine": RiskEngine(load_risk_policy()),
        "executor": Executor(),
        "equity_tracker": ExecutionEquityTracker(),
        "policy": load_risk_policy(),
    }


async def test_entry_signal_submits_and_fills_order_advances_to_holding(pool):
    user_id = await create_test_user(pool)
    # SMA(close=50, 5기간) = 50 < 100 → 진입 조건 항상 충족.
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 30,
        place_order_result_status=OrderStatus.FILLED,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 1
    async with pool.acquire() as conn:
        execution = await conn.fetchrow(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
        order = await conn.fetchrow(
            "SELECT status, side, quantity FROM orders WHERE execution_id = $1", execution_id
        )
    assert execution["fsm_state"] == "HOLDING"
    assert order["status"] == "FILLED"
    assert order["side"] == "BUY"
    # allocated_capital(1000) / current_price(50) = 20
    assert order["quantity"] == Decimal("20.0000000000")


async def test_no_signal_tick_does_nothing(pool):
    user_id = await create_test_user(pool)
    # SMA(50) < -1 은 항상 거짓 — 진입 조건 미충족.
    execution_id = await _create_execution(pool, user_id, entry_threshold=-1.0)
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 30)

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"


async def test_risk_rejection_leaves_fsm_state_idle_for_retry(pool):
    """자본배분 상한 초과(미인증 전략 10%인데 50% 요청) — RiskEngine이
    거부하면 fsm_state는 IDLE 그대로 남아 다음 틱에 재평가돼야 한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(
        pool, user_id, entry_threshold=100.0, allocated_capital=Decimal("5000")
    )
    adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 30,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"


async def test_paused_execution_is_skipped(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET status = 'PAUSED', paused_by = 'SAFETY_LAYER' "
            "WHERE id = $1",
            execution_id,
        )
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 30)

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0


async def test_safety_pause_mid_tick_blocks_order_submission(pool):
    """레드팀 #23-a 회귀 테스트 — tick 시작 시점(_load_execution_context)에는
    paused_by가 비어 있었지만, 신호 평가·RiskEngine 검사를 거치는 사이
    Watchdog가 안전정지를 걸면(get_balance() 호출 시점에 주입해 시뮬레이션)
    이번 tick은 주문을 제출하지 않아야 하고, fsm_state도 PENDING류에
    갇히지 않고 원래 상태(IDLE)를 유지해야 한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)

    class _PausingAdapter(FakeExchangeAdapter):
        async def get_balance(self, asset: str | None = None):  # noqa: ANN001
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE strategy_executions SET status = 'PAUSED', "
                    "paused_by = 'SAFETY_LAYER' WHERE id = $1",
                    execution_id,
                )
            return await super().get_balance(asset)

    adapter = _PausingAdapter(
        closes=[Decimal("50")] * 30,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"  # PENDING류에 갇히지 않음 — 되돌림이 필요 없는 설계


async def test_paused_execution_still_checks_pending_order_fill(pool):
    """레드팀 #23-c 회귀 테스트 — 주문 제출 직후 일시정지된 실행이라도,
    이미 제출한 주문의 체결 여부는 계속 확인해야 한다. 정지 체크가
    PENDING-fill-check보다 먼저 실행되면 이 확인 자체가 영원히 스킵된다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)

    # 1틱: 주문 제출 직후 아직 미체결(SUBMITTED) → fsm_state=BUY_ORDER_PENDING.
    submit_adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 30,
        place_order_result_status=OrderStatus.SUBMITTED,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )
    await run_execution_tick(pool, submit_adapter, execution_id, **_engines())
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
        # Watchdog가 그 사이 이 실행을 안전정지시켰다고 가정.
        await conn.execute(
            "UPDATE strategy_executions SET status = 'PAUSED', paused_by = 'SAFETY_LAYER' "
            "WHERE id = $1",
            execution_id,
        )
    assert fsm_state == "BUY_ORDER_PENDING"

    # 2틱: 정지된 상태에서도 미체결 주문이 실제로는 체결됐는지 확인돼야 한다.
    fill_adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 30, get_order_status=OrderStatus.FILLED
    )
    await run_execution_tick(pool, fill_adapter, execution_id, **_engines())

    async with pool.acquire() as conn:
        order_status = await conn.fetchval(
            "SELECT status FROM orders WHERE execution_id = $1", execution_id
        )
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert order_status == "FILLED"
    assert fsm_state == "HOLDING"  # 체결 반영으로 PENDING을 벗어남 — 정지 중에도 이뤄져야 함


async def test_fsm_state_writer_raises_on_concurrent_state_change(pool):
    """레드팀 #2026-09-02-22 회귀 테스트 — writer가 읽었던 expected_state와
    실제 DB 값이 다르면(다른 tick이 먼저 바꿈) ConcurrencyConflictError를
    던지고 아무것도 쓰지 않아야 한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET fsm_state = 'BUY_ORDER_PENDING' WHERE id = $1",
            execution_id,
        )

    writer = await _make_fsm_state_writer(pool)
    with pytest.raises(ConcurrencyConflictError):
        # 이 tick은 IDLE을 읽었다고 주장하지만 실제론 이미 BUY_ORDER_PENDING.
        await writer(execution_id, FSMState.IDLE, FSMState.BUY_ORDER_PENDING)

    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "BUY_ORDER_PENDING"  # 충돌한 쓰기는 반영되지 않음


async def test_concurrent_tick_race_only_submits_one_order(pool):
    """레드팀 #2026-09-02-22 회귀 테스트 — get_balance() 호출 시점에 "다른
    tick"이 이미 이 execution을 BUY_ORDER_PENDING으로 선점했다고 가정하면
    (run_execution_tick이 IDLE을 읽은 *이후*, 자기 자신의 조건부 쓰기 *전*
    끼어든 상황을 시뮬레이션), 이 tick의 조건부 쓰기가 충돌해야 하고
    Executor.execute()까지 가서 실제 주문을 내면 안 된다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)

    class _RacingAdapter(FakeExchangeAdapter):
        async def get_balance(self, asset: str | None = None):  # noqa: ANN001
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE strategy_executions SET fsm_state = 'BUY_ORDER_PENDING' "
                    "WHERE id = $1",
                    execution_id,
                )
            return await super().get_balance(asset)

    adapter = _RacingAdapter(
        closes=[Decimal("50")] * 30,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "BUY_ORDER_PENDING"  # "다른 tick"이 쓴 값 그대로 — 이 tick이 덮어쓰지 않음


async def test_cancelled_order_reverts_fsm_state_instead_of_getting_stuck(pool):
    """PM 배정(agent-platform-12, 2026-09-02, 레드팀 #39) 회귀 테스트 —
    이전엔 PENDING 상태에서 마지막 주문이 체결 없이 종결(CANCELLED 등)되면
    fsm_state가 영원히 PENDING에 갇혀 이후 어떤 신호도 재평가되지 않았다.
    지금은 신호평가로 그 PENDING에 들어오기 전 상태(IDLE)로 되돌아가야
    한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    async with pool.acquire() as conn:
        execution = await conn.fetchrow(
            "SELECT strategy_id, strategy_version FROM strategy_executions WHERE id = $1",
            execution_id,
        )
        await conn.execute(
            "UPDATE strategy_executions SET fsm_state = 'BUY_ORDER_PENDING' WHERE id = $1",
            execution_id,
        )
        await conn.execute(
            """
            INSERT INTO orders (
                order_id, user_id, client_order_id, exchange_order_id, strategy_id,
                strategy_version, execution_id, symbol, exchange, side, order_type,
                quantity, status, filled_quantity, is_liquidation, asset_class
            ) VALUES (
                gen_random_uuid(), $1, $2, 'ex-cancelled-1', $3, $4,
                $5, 'BTC/USDT', 'bitget', 'BUY', 'MARKET', 0.01, 'CANCELLED', 0, false, 'CRYPTO'
            )
            """,
            user_id,
            f"cancelled-{uuid.uuid4().hex}",
            execution["strategy_id"],
            execution["strategy_version"],
            execution_id,
        )

    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 30)
    await run_execution_tick(pool, adapter, execution_id, **_engines())

    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"


async def test_equity_baseline_persists_and_survives_simulated_restart(pool):
    """PM 배정 ③(agent-platform-12, 2026-09-02) 회귀 테스트 — 이전엔
    일손실/MDD 기준점이 프로세스 메모리에만 있어 재시작하면 유실됐다.
    "재시작"은 매번 새 ExecutionEquityTracker(빈 메모리)로 tick을 다시
    부르는 것으로 시뮬레이션한다(_engines()가 매번 새 인스턴스를 만듦).
    RiskEngine이 항상 거부하도록(자본배분 상한 초과, test_risk_rejection_
    leaves_fsm_state_idle_for_retry와 동일 설정) fsm_state를 IDLE에
    묶어둬 매 tick마다 assemble_account_state가 반복 호출되게 한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(
        pool, user_id, entry_threshold=100.0, allocated_capital=Decimal("5000")
    )
    first_tick_adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 30,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )

    # "프로세스 1" — 최초 tick, 빈 메모리 tracker로 기준점을 처음 만든다.
    await run_execution_tick(pool, first_tick_adapter, execution_id, **_engines())

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT equity_day_start_date, equity_day_start_value, equity_peak_value "
            "FROM strategy_executions WHERE id = $1",
            execution_id,
        )
    assert row["equity_day_start_date"] is not None
    assert row["equity_day_start_value"] == Decimal("10000.0000000000")
    assert row["equity_peak_value"] == Decimal("10000.0000000000")

    # "프로세스 2"(재시작 시뮬레이션) — 새 tracker(빈 메모리)로 다시 tick,
    # 이번엔 잔고가 바뀐 상태(9000)다. DB에서 기준점을 seed()로 복구해야
    # "오늘 시작"이 지금 이 순간(9000)으로 리셋되지 않고 원래 10000을
    # 그대로 이어받는다 — 그래야 오늘 이미 나던 손실이 재시작으로
    # 사라지지 않는다.
    second_tick_adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 30,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("9000"), available=Decimal("9000")
        ),
    )
    await run_execution_tick(pool, second_tick_adapter, execution_id, **_engines())

    async with pool.acquire() as conn:
        row_after_restart = await conn.fetchrow(
            "SELECT equity_day_start_date, equity_day_start_value, equity_peak_value "
            "FROM strategy_executions WHERE id = $1",
            execution_id,
        )
    # 시작 기준점은 그대로 10000 유지(재시작으로 리셋되지 않음).
    assert row_after_restart["equity_day_start_date"] == row["equity_day_start_date"]
    assert row_after_restart["equity_day_start_value"] == Decimal("10000.0000000000")
    # peak은 10000 그대로(9000 < 10000이라 갱신 안 됨) — 이것도 기준점이
    # 실제로 이어받아졌다는 방증.
    assert row_after_restart["equity_peak_value"] == Decimal("10000.0000000000")
