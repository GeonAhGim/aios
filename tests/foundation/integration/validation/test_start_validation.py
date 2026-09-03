"""FND-04 Strategy Validation 통합테스트 — 실제 dev DB 대상. 71번 §7 "정상
흐름 + negative test". `strategy_builder_service.py`의 실제 9.9 절대원칙
생애주기와 FND-10 백테스트 엔진을 함께 검증한다(둘 다 이 리프가 새로
만들지 않은 기존/병행 구현이며, 이 테스트는 그 둘을 잇는 배선을 검증)."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.validation.adapters.postgres_repository import PostgresValidationRepository
from src.foundation.validation.application.start_validation import (
    StrategyNotEligibleForValidationError,
    ValidationAlreadyInProgressError,
    start_validation,
)
from src.foundation.validation.contracts.v1 import Outcome, RunState, StartValidationCommand
from src.services.strategy_builder_service import StrategyBuilderService
from tests.integration.conftest import create_test_user

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def validation_repo(pool):
    return PostgresValidationRepository(pool)


@pytest.fixture
def strategy_service(pool):
    return StrategyBuilderService(pool)


@dataclass
class _FakeIndicatorResult:
    values: list[float | None]


class _FakePriceIndicatorService:
    """"PRICE" 키를 창(window)의 마지막 종가로 되돌린다 — TA-Lib 의존 없이
    오케스트레이션만 검증(tests/foundation/unit/backtest/test_run_backtest.py와
    동일 원칙)."""

    def calculate(
        self, indicator: str, candles: list[Candle], **params: int
    ) -> _FakeIndicatorResult:
        assert indicator == "PRICE"
        return _FakeIndicatorResult(values=[float(candles[-1].close)])


def _re_entry_bug_fsm_config() -> FSMStrategyConfig:
    """F-04 회귀 — 의도적으로 버그가 있는 FSM: HOLDING에서 항상 참인
    조건으로 다시 BUY_ORDER_PENDING(진입 방향)으로 나가는 전이를 둔다.
    체결 후 HOLDING인데 또 BUY 신호가 나와 PortfolioEngine이
    PortfolioEngineError를 던지고, run_backtest()가 그걸 경고로 캐치한다
    (application 계층 raise가 아님 — 그래서 지금까지 hard_fail_reasons가
    항상 빈 튜플이어도 아무도 못 알아챘다)."""
    return FSMStrategyConfig(
        strategy_id="test-strategy",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.BUY_ORDER_PENDING, FSMState.HOLDING],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE,
                to_state=FSMState.BUY_ORDER_PENDING,
                condition="PRICE > 0",
            ),
            FSMTransition(
                from_state=FSMState.BUY_ORDER_PENDING,
                to_state=FSMState.HOLDING,
                condition="ORDER_FILLED",
            ),
            FSMTransition(
                from_state=FSMState.HOLDING,
                to_state=FSMState.BUY_ORDER_PENDING,
                condition="PRICE > 0",
            ),
        ],
        author_agent="test",
    )


def _never_fires_fsm_config() -> FSMStrategyConfig:
    """PRICE > 10000 조건은 아래 테스트 bar들에서 절대 참이 되지 않는다 —
    거래 없이 evaluate()만 반복 호출되는 경로를 검증한다."""
    return FSMStrategyConfig(
        strategy_id="test-strategy",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.BUY_ORDER_PENDING],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE,
                to_state=FSMState.BUY_ORDER_PENDING,
                condition="PRICE > 10000",
            ),
        ],
        author_agent="test",
    )


def _bars(count: int = 5) -> list[Candle]:
    return [
        Candle(
            symbol="BTC/USDT",
            exchange="bitget",
            timeframe="1h",
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("1"),
            open_time=_T0 + timedelta(hours=i),
            close_time=_T0 + timedelta(hours=i),
        )
        for i in range(count)
    ]


async def _strategy_in_backtesting(
    pool, strategy_service, fsm_config: FSMStrategyConfig | None = None
) -> tuple[UUID, str, str]:
    """소유자를 만들고, GENERATED로 전략을 저장한 뒤 BACKTESTING까지 한 칸
    전이시킨다(9.9 절대원칙 순서 그대로, 지름길 없음)."""
    owner_id = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    fsm_definition = (fsm_config or _never_fires_fsm_config()).model_dump(mode="json")
    await strategy_service.save_strategy(
        owner_id,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition=fsm_definition,
    )
    await strategy_service.transition_lifecycle(strategy_id, "1.0.0", "BACKTESTING")
    return owner_id, strategy_id, "1.0.0"


def _command(strategy_id: str, version: str, **overrides) -> StartValidationCommand:
    defaults = dict(
        strategy_id=strategy_id,
        strategy_version=version,
        cost_model_fee_bps=Decimal("5"),
        cost_model_slippage_bps=Decimal("2"),
        warmup_bars=0,
        periods_per_year=252,
        initial_equity=Decimal("10000"),
    )
    defaults.update(overrides)
    return StartValidationCommand(**defaults)


async def test_validation_on_generated_strategy_is_rejected(
    pool, validation_repo, strategy_service
):
    """9.9 절대원칙 — GENERATED에서 바로 검증을 시작할 수 없다(BACKTESTING을
    거쳐야 한다)."""
    owner_id = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    fsm_definition = _never_fires_fsm_config().model_dump(mode="json")
    await strategy_service.save_strategy(
        owner_id,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition=fsm_definition,
    )

    with pytest.raises(StrategyNotEligibleForValidationError):
        await start_validation(
            validation_repo,
            strategy_service,
            owner_user_id=owner_id,
            command=_command(strategy_id, "1.0.0"),
            bars=_bars(),
            indicator_service=_FakePriceIndicatorService(),
        )


async def test_successful_validation_advances_lifecycle_to_validating(
    pool, validation_repo, strategy_service
):
    owner_id, strategy_id, version = await _strategy_in_backtesting(pool, strategy_service)

    view = await start_validation(
        validation_repo,
        strategy_service,
        owner_user_id=owner_id,
        command=_command(strategy_id, version),
        bars=_bars(),
        indicator_service=_FakePriceIndicatorService(),
    )

    assert view.state == RunState.SUCCEEDED
    assert view.outcome in (Outcome.PASS, Outcome.PASS_WITH_OBLIGATIONS)
    assert view.metrics is not None

    detail = await strategy_service.get_strategy(owner_id, strategy_id, version)
    assert detail.lifecycle_status == "VALIDATING"


async def test_zero_cost_model_passes_with_obligations(pool, validation_repo, strategy_service):
    owner_id, strategy_id, version = await _strategy_in_backtesting(pool, strategy_service)

    view = await start_validation(
        validation_repo,
        strategy_service,
        owner_user_id=owner_id,
        command=_command(
            strategy_id,
            version,
            cost_model_fee_bps=Decimal("0"),
            cost_model_slippage_bps=Decimal("0"),
        ),
        bars=_bars(),
        indicator_service=_FakePriceIndicatorService(),
    )

    assert view.outcome == Outcome.PASS_WITH_OBLIGATIONS
    assert view.obligations != []


async def test_identical_repeat_request_returns_cached_result_without_rerunning(
    pool, validation_repo, strategy_service
):
    """STR-001/STR-007 재현성·멱등성 — 같은 정확한 입력으로 두 번 호출하면
    같은 run_id를 반환하고, 두 번째 호출은 이미 VALIDATING으로 넘어간 뒤라도
    "상태가 아니다"로 거부되지 않는다."""
    owner_id, strategy_id, version = await _strategy_in_backtesting(pool, strategy_service)
    command = _command(strategy_id, version)
    bars = _bars()

    first = await start_validation(
        validation_repo,
        strategy_service,
        owner_user_id=owner_id,
        command=command,
        bars=bars,
        indicator_service=_FakePriceIndicatorService(),
    )
    second = await start_validation(
        validation_repo,
        strategy_service,
        owner_user_id=owner_id,
        command=command,
        bars=bars,
        indicator_service=_FakePriceIndicatorService(),
    )

    assert first.run_id == second.run_id


async def test_concurrent_identical_requests_only_one_computes_the_rest_attach(
    pool, validation_repo, strategy_service
):
    """105번 §4 형태 A의 INSERT 버전 — UNIQUE 제약이 단일 소유자를 보장하고,
    나머지는 완료된 결과에 붙거나(둘 다 짧게 끝나는 이 테스트에선 대부분
    이 경로) 진행 중 신호를 받는다(ValidationAlreadyInProgressError는 있어도
    괜찮다 — 절대 서로 다른 두 run이 생기면 안 된다는 게 핵심 불변조건)."""
    owner_id, strategy_id, version = await _strategy_in_backtesting(pool, strategy_service)
    command = _command(strategy_id, version)
    bars = _bars()

    async def attempt():
        return await start_validation(
            validation_repo,
            strategy_service,
            owner_user_id=owner_id,
            command=command,
            bars=bars,
            indicator_service=_FakePriceIndicatorService(),
        )

    results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)

    run_ids = {
        r.run_id
        for r in results
        if not isinstance(r, Exception)
    }
    assert len(run_ids) <= 1  # 서로 다른 두 run이 생기는 것만은 절대 안 됨
    for r in results:
        if isinstance(r, Exception):
            assert isinstance(r, (ValidationAlreadyInProgressError, ConcurrencyConflictError))

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM strategy_validation_run WHERE strategy_id = $1", strategy_id
        )
    assert count == 1


async def test_backtest_engine_error_fails_validation_and_blocks_lifecycle(
    pool, validation_repo, strategy_service
):
    """F-04 적대적 테스트 — 임계 미달(여기서는 실제 백테스트 재생 오류)
    입력이면 outcome이 FAIL이고 hard_fail_reasons가 비어있지 않아야
    한다. 고쳐지기 전에는 evaluate_validation_policy가 항상
    hard_fail_reasons=()를 만들어 이 상태에 절대 도달할 수 없었다.
    PASS/PASS_WITH_OBLIGATIONS만 VALIDATING으로 전이시키는 기존 가드
    (start_validation.py) 덕분에 FAIL이면 전략은 BACKTESTING에 그대로
    남아야 한다."""
    owner_id, strategy_id, version = await _strategy_in_backtesting(
        pool, strategy_service, fsm_config=_re_entry_bug_fsm_config()
    )

    view = await start_validation(
        validation_repo,
        strategy_service,
        owner_user_id=owner_id,
        command=_command(strategy_id, version),
        bars=_bars(count=4),
        indicator_service=_FakePriceIndicatorService(),
    )

    assert view.outcome == Outcome.FAIL
    assert view.hard_fail_reasons != []

    detail = await strategy_service.get_strategy(owner_id, strategy_id, version)
    assert detail.lifecycle_status == "BACKTESTING"
