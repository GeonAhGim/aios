"""L50 — validation 컨텍스트 관측성 배선(로그 필드·메트릭 카운터) 증명.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 L50 DoD. `start_validation()`이
`metrics: MetricsPort` 계측 지점(PLT-10 패턴)과 구조화 로그를 실제로 호출하는지 실DB
대상으로 검증한다. 픽스처는 `test_start_validation.py`의 것을 그대로 옮겨온다(같은
디렉터리에 공용 conftest.py가 없어 로컬로 재선언 — 모듈 docstring 참조)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.observability.metric_names import (
    VALIDATION_RUN_COUNT_TOTAL,
    VALIDATION_RUN_DURATION_SECONDS,
)
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.validation.adapters.postgres_repository import PostgresValidationRepository
from src.foundation.validation.application.start_validation import (
    StrategyNotEligibleForValidationError,
    start_validation,
)
from src.foundation.validation.contracts.v1 import Outcome, StartValidationCommand
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
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def validation_repo(pool):
    return PostgresValidationRepository(pool)


@pytest.fixture
def strategy_service(pool):
    return StrategyBuilderService(pool)


@dataclass
class _SpyMetrics:
    counters: list[tuple[str, dict[str, str] | None]] = field(default_factory=list)
    observations: list[tuple[str, float, dict[str, str] | None]] = field(default_factory=list)

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.counters.append((name, labels))

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self.observations.append((name, value, labels))

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None


@dataclass
class _FakeIndicatorResult:
    values: list[float | None]


class _FakePriceIndicatorService:
    def calculate(
        self, indicator: str, candles: list[Candle], **params: int
    ) -> _FakeIndicatorResult:
        assert indicator == "PRICE"
        return _FakeIndicatorResult(values=[float(candles[-1].close)])


def _never_fires_fsm_config() -> FSMStrategyConfig:
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


async def _strategy_in_backtesting(pool, strategy_service) -> tuple[UUID, str, str]:
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


async def test_successful_validation_records_outcome_counter_and_duration(
    pool, validation_repo, strategy_service
):
    owner_id, strategy_id, version = await _strategy_in_backtesting(pool, strategy_service)
    spy = _SpyMetrics()

    view = await start_validation(
        validation_repo,
        strategy_service,
        owner_user_id=owner_id,
        command=_command(strategy_id, version),
        bars=_bars(),
        indicator_service=_FakePriceIndicatorService(),
        metrics=spy,
    )

    assert view.outcome in (Outcome.PASS, Outcome.PASS_WITH_OBLIGATIONS)
    assert (VALIDATION_RUN_COUNT_TOTAL, {"outcome": view.outcome.value}) in spy.counters
    durations = [o for o in spy.observations if o[0] == VALIDATION_RUN_DURATION_SECONDS]
    assert len(durations) == 1
    _, value, labels = durations[0]
    assert value >= 0.0
    assert labels == {"outcome": view.outcome.value}


async def test_ineligible_strategy_records_no_run_metric(pool, validation_repo, strategy_service):
    """negative — GENERATED 상태(아직 run이 생성되지 않은 경로)에서 거부되면
    run count/duration 어느 것도 기록되지 않아야 한다(오탐 방지)."""
    owner_id = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await strategy_service.save_strategy(
        owner_id,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition=_never_fires_fsm_config().model_dump(mode="json"),
    )
    spy = _SpyMetrics()

    with pytest.raises(StrategyNotEligibleForValidationError):
        await start_validation(
            validation_repo,
            strategy_service,
            owner_user_id=owner_id,
            command=_command(strategy_id, "1.0.0"),
            bars=_bars(),
            indicator_service=_FakePriceIndicatorService(),
            metrics=spy,
        )

    assert spy.counters == []
    assert spy.observations == []


async def test_without_metrics_arg_defaults_to_null_metrics_and_does_not_crash(
    pool, validation_repo, strategy_service
):
    """negative — 기존 호출부(메트릭 인자 없이 호출)는 NullMetrics로 대체돼
    아무 영향 없이 계속 동작해야 한다(하위호환)."""
    owner_id, strategy_id, version = await _strategy_in_backtesting(pool, strategy_service)

    view = await start_validation(
        validation_repo,
        strategy_service,
        owner_user_id=owner_id,
        command=_command(strategy_id, version),
        bars=_bars(),
        indicator_service=_FakePriceIndicatorService(),
    )

    assert view.outcome in (Outcome.PASS, Outcome.PASS_WITH_OBLIGATIONS)


async def test_completed_log_field_snapshot(pool, validation_repo, strategy_service, caplog):
    """로그 필드 스냅샷 테스트 — `validation_run_completed` 이벤트가
    `extra={"event":..., "duration_ms":..., "payload": {...}}` 채널로 정확히
    어떤 필드를 싣는지 스냅샷으로 고정한다."""
    owner_id, strategy_id, version = await _strategy_in_backtesting(pool, strategy_service)

    with caplog.at_level(
        logging.INFO, logger="src.foundation.validation.application.start_validation"
    ):
        view = await start_validation(
            validation_repo,
            strategy_service,
            owner_user_id=owner_id,
            command=_command(strategy_id, version),
            bars=_bars(),
            indicator_service=_FakePriceIndicatorService(),
        )

    records = [r for r in caplog.records if getattr(r, "event", None) == "validation_run_completed"]
    assert len(records) == 1
    record = records[0]
    assert isinstance(record.duration_ms, int)
    assert record.duration_ms >= 0
    payload = record.payload
    assert set(payload.keys()) == {
        "run_id",
        "strategy_id",
        "outcome",
        "hard_fail_reason_count",
    }
    assert payload["run_id"] == str(view.run_id)
    assert payload["strategy_id"] == strategy_id
    assert payload["outcome"] == view.outcome.value
    assert payload["hard_fail_reason_count"] == len(view.hard_fail_reasons)
