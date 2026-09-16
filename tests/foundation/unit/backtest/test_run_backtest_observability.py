"""L50 — backtest 컨텍스트 관측성 배선(로그 필드·메트릭 카운터) 증명.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 L50 DoD. `run_backtest()`가
`metrics: MetricsPort` 계측 지점(PLT-10 패턴)과 구조화 로그(`extra={"event":...,
"payload": {...}}`, 108 §2)를 정상/실패 두 경로 모두에서 실제로 호출하는지 검증한다.
FSM 픽스처는 `test_run_backtest.py`와 같은 원칙(TA-Lib 대신 가짜 IndicatorService)을
쓰되, 그 파일의 모듈-private 헬퍼를 재사용하지 않고 이 파일에 필요한 최소 형태로
다시 선언한다(관측 축과 오케스트레이션 축의 파일 분리, submit.py 계열 선례와 동일)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.core.observability.metric_names import (
    BACKTEST_RUN_COUNT_TOTAL,
    BACKTEST_RUN_DURATION_SECONDS,
)
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.backtest.application.run_backtest import BacktestRunError, run_backtest
from src.foundation.backtest.domain.models import BacktestConfig, CostModel

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ZERO_COST = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))


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


def _bar(*, open_price: str, close_price: str, index: int) -> Candle:
    ts = _T0 + timedelta(hours=index)
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal(open_price),
        high=max(Decimal(open_price), Decimal(close_price)),
        low=min(Decimal(open_price), Decimal(close_price)),
        close=Decimal(close_price),
        volume=Decimal("1"),
        open_time=ts,
        close_time=ts,
    )


def _config(*, warmup_bars: int = 0) -> BacktestConfig:
    return BacktestConfig(
        strategy_id="test-strategy",
        strategy_version="v1",
        initial_equity=Decimal("1000"),
        cost_model=_ZERO_COST,
        warmup_bars=warmup_bars,
        periods_per_year=252,
    )


def _never_signals_fsm_config() -> FSMStrategyConfig:
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
                condition="PRICE > 1000000",
            ),
        ],
        author_agent="test",
    )


def _missing_order_filled_transition_fsm_config() -> FSMStrategyConfig:
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
                condition="PRICE > 105",
            ),
        ],
        author_agent="test",
    )


def _bars(n: int = 5) -> list[Candle]:
    return [_bar(index=i, open_price="100", close_price="100") for i in range(n)]


def test_completed_run_records_completed_counter_and_duration() -> None:
    spy = _SpyMetrics()

    run_backtest(
        _config(),
        _never_signals_fsm_config(),
        _bars(),
        indicator_service=_FakePriceIndicatorService(),
        metrics=spy,
    )

    assert (BACKTEST_RUN_COUNT_TOTAL, {"outcome": "completed"}) in spy.counters
    durations = [o for o in spy.observations if o[0] == BACKTEST_RUN_DURATION_SECONDS]
    assert len(durations) == 1
    _, value, labels = durations[0]
    assert value >= 0.0
    assert labels == {"outcome": "completed"}


def test_insufficient_warmup_records_failed_outcome_not_completed() -> None:
    """negative — warmup 부족으로 거부되면 "insufficient_warmup" 카운터만
    기록되고 "completed"는 절대 기록되지 않아야 한다."""
    spy = _SpyMetrics()

    with pytest.raises(BacktestRunError):
        run_backtest(
            _config(warmup_bars=10),
            _never_signals_fsm_config(),
            _bars(n=3),
            indicator_service=_FakePriceIndicatorService(),
            metrics=spy,
        )

    assert (BACKTEST_RUN_COUNT_TOTAL, {"outcome": "insufficient_warmup"}) in spy.counters
    assert (BACKTEST_RUN_COUNT_TOTAL, {"outcome": "completed"}) not in spy.counters


def test_missing_order_filled_transition_records_failed_counter_and_duration() -> None:
    """실패 주입 — FSM 컴파일러 계약 위반(ORDER_FILLED로 나가는 전이 없음)을
    실제로 주입해, "failed" 카운터·지속시간 관측이 정확히 1회 기록되고
    "completed"는 기록되지 않음을 확인한다."""
    spy = _SpyMetrics()
    bars = [
        _bar(index=0, open_price="100", close_price="100"),
        _bar(index=1, open_price="100", close_price="110"),  # PRICE>105 신호 발생
        _bar(index=2, open_price="112", close_price="111"),
    ]

    with pytest.raises(BacktestRunError, match="ORDER_FILLED"):
        run_backtest(
            _config(),
            _missing_order_filled_transition_fsm_config(),
            bars,
            indicator_service=_FakePriceIndicatorService(),
            metrics=spy,
        )

    assert (BACKTEST_RUN_COUNT_TOTAL, {"outcome": "failed"}) in spy.counters
    durations = [o for o in spy.observations if o[0] == BACKTEST_RUN_DURATION_SECONDS]
    assert len(durations) == 1
    assert durations[0][2] == {"outcome": "failed"}
    assert (BACKTEST_RUN_COUNT_TOTAL, {"outcome": "completed"}) not in spy.counters


def test_without_metrics_arg_defaults_to_null_metrics_and_does_not_crash() -> None:
    """negative — 기존 호출부(메트릭 인자 없이 호출)는 NullMetrics로 대체돼
    아무 영향 없이 계속 동작해야 한다(하위호환)."""
    result = run_backtest(
        _config(),
        _never_signals_fsm_config(),
        _bars(),
        indicator_service=_FakePriceIndicatorService(),
    )
    assert result.metrics is not None


def test_completed_log_field_snapshot(caplog: pytest.LogCaptureFixture) -> None:
    """로그 필드 스냅샷 테스트 — `backtest_run_completed` 이벤트가
    `extra={"event":..., "duration_ms":..., "payload": {...}}` 채널로 정확히
    어떤 필드를 싣는지 스냅샷으로 고정한다(필드 추가·누락 모두 이 테스트가 잡는다)."""
    bars = _bars(n=5)
    with caplog.at_level(logging.INFO, logger="src.foundation.backtest.application.run_backtest"):
        run_backtest(
            _config(),
            _never_signals_fsm_config(),
            bars,
            indicator_service=_FakePriceIndicatorService(),
        )

    records = [r for r in caplog.records if getattr(r, "event", None) == "backtest_run_completed"]
    assert len(records) == 1
    record = records[0]
    assert isinstance(record.duration_ms, int)
    assert record.duration_ms >= 0
    payload = record.payload
    assert set(payload.keys()) == {"strategy_id", "bar_count", "fill_count", "warning_count"}
    assert payload["strategy_id"] == "test-strategy"
    assert payload["bar_count"] == len(bars)
    assert payload["fill_count"] == 0
    assert payload["warning_count"] == 1  # zero-cost 모델 경고(warn_if_zero_cost)
