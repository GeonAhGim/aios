"""L50 — 백테스트 처리량 벤치마크(ADR-2026-09-09-C §축별 성능 예산 "백테스트
1개월 M1 1심볼 3초").

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 L50 DoD("벤치마크 단언
통과"). `run_backtest()`(L31)에 L50이 새로 배선한 `metrics: MetricsPort` 계측
지점(`tests/foundation/unit/backtest/test_run_backtest_observability.py`)이 실제
실측 시간과 일치하는 값을 관측하는지, 그리고 그 실측 시간이 로컬 성능 예산
바닥선 아래인지를 함께 검증한다.

게이트 적색 재현(D2 4번째 항목)은 이 파일에서 새로 만들지 않는다 — 같은
컨텍스트(backtest 처리량)에 대해 이미
`tests/foundation/unit/backtest/test_run_backtest.py::
test_quadratic_window_rebuild_is_a_known_gate_red_against_monthly_budget`가
1개월치(43,200 bar) 외삽 기준으로 O(n^2) 결함을 수치로 고정해 존재한다(L31 D2
deepen, task-3356) — 중복 대신 여기서는 교차 참조만 남긴다."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.core.observability.metric_names import BACKTEST_RUN_DURATION_SECONDS
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.backtest.application.run_backtest import run_backtest
from src.foundation.backtest.domain.models import BacktestConfig, CostModel

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ZERO_COST = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))


@dataclass
class _SpyMetrics:
    observations: list[tuple[str, float, dict[str, str] | None]] = field(default_factory=list)

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        return None

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self.observations.append((name, value, labels))

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None


@dataclass
class _FakeIndicatorResult:
    values: list[float | None]


class _FakePriceIndicatorService:
    """실제 TA-Lib 대신 "PRICE"=마지막 종가만 제공 — 이 벤치마크가 재는 건
    지표 계산 비용이 아니라 재생 루프 오케스트레이션 처리량이다
    (test_run_backtest.py와 동일 원칙, 모듈 docstring 참조)."""

    def calculate(
        self, indicator: str, candles: list[Candle], **params: int
    ) -> _FakeIndicatorResult:
        assert indicator == "PRICE"
        return _FakeIndicatorResult(values=[float(candles[-1].close)])


def _synthetic_bars(n: int) -> list[Candle]:
    return [
        Candle(
            symbol="BTC/USDT",
            exchange="bitget",
            timeframe="1m",
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("1"),
            open_time=_T0 + timedelta(minutes=i),
            close_time=_T0 + timedelta(minutes=i),
        )
        for i in range(n)
    ]


def _config() -> BacktestConfig:
    return BacktestConfig(
        strategy_id="bench-strategy",
        strategy_version="v1",
        initial_equity=Decimal("10000"),
        cost_model=_ZERO_COST,
        warmup_bars=0,
        periods_per_year=525600,
    )


def _never_signals_fsm_config() -> FSMStrategyConfig:
    """신호가 한 번도 나오지 않는 FSM — 이 벤치마크는 체결 로직이 아니라
    "매 bar마다 반드시 지나가는" market_state 조립 경로의 처리량을 잰다."""
    return FSMStrategyConfig(
        strategy_id="bench-strategy",
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


def test_throughput_meets_local_budget_floor_and_metric_matches_wall_clock() -> None:
    """성능 단언(수치): ADR-2026-09-09-C "백테스트 1개월 M1 1심볼 3초"의 극히
    일부 구간(2,000 bar ≈ 1.4일치 M1)조차 여유 있게 끝나야 한다 — 실측
    기준선(로컬, fake indicator) 대비 8배 이상 여유를 둔 1.0초를 바닥선으로
    건다(tests/foundation/unit/backtest/test_run_backtest.py의 동일 예산과
    같은 값 — L50이 그 실측치를 재확인하고, 새로 배선한 메트릭 관측이 그
    벽시계 실측과 일치하는지까지 함께 증명한다)."""
    bars = _synthetic_bars(2000)
    fsm = _never_signals_fsm_config()
    spy = _SpyMetrics()

    wall_start = time.perf_counter()
    run_backtest(_config(), fsm, bars, indicator_service=_FakePriceIndicatorService(), metrics=spy)
    wall_elapsed = time.perf_counter() - wall_start

    assert wall_elapsed < 1.0, f"2,000 bar 처리에 {wall_elapsed:.3f}s — 1.0s 예산 초과"

    durations = [o for o in spy.observations if o[0] == BACKTEST_RUN_DURATION_SECONDS]
    assert len(durations) == 1
    _, observed, labels = durations[0]
    assert labels == {"outcome": "completed"}
    # 계측 지점과 이 테스트의 벽시계 측정은 서로 다른 time.monotonic() 호출
    # 구간이라 완전히 같을 수 없다 — 같은 자릿수(오버헤드 배 이내)인지만 본다.
    assert observed <= wall_elapsed * 2, (
        f"관측된 duration({observed:.3f}s)이 벽시계 실측({wall_elapsed:.3f}s)과 "
        "크게 어긋난다 — 계측 지점이 잘못된 구간을 재고 있을 수 있다"
    )
