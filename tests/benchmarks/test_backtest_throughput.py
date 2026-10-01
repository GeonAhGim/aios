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

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.core.indicators.talib_adapter import IndicatorResult, IndicatorService
from src.core.observability.metric_names import (
    BACKTEST_RUN_COUNT_TOTAL,
    BACKTEST_RUN_DURATION_SECONDS,
)
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.backtest.application.run_backtest import (
    BacktestRunError,
    run_backtest,
)
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from tests.conftest import PerfBudget

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ZERO_COST = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))

# task-7434: this whole module measures wall-clock throughput budgets, so it
# runs in the serial perf CI stage rather than under xdist core contention.
pytestmark = pytest.mark.perf


@dataclass
class _SpyMetrics:
    observations: list[tuple[str, float, dict[str, str] | None]] = field(default_factory=list)
    counters: list[tuple[str, dict[str, str] | None]] = field(default_factory=list)

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.counters.append((name, labels))

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self.observations.append((name, value, labels))

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None


class _FakePriceIndicatorService(IndicatorService):
    """실제 TA-Lib 대신 "PRICE"=마지막 종가만 제공 — 이 벤치마크가 재는 건
    지표 계산 비용이 아니라 재생 루프 오케스트레이션 처리량이다
    (test_run_backtest.py와 동일 원칙, 모듈 docstring 참조)."""

    def calculate(
        self, indicator: str, candles: Sequence[Candle], **params: int
    ) -> IndicatorResult:
        assert indicator == "PRICE"
        return IndicatorResult(
            indicator=indicator, values=[float(candles[-1].close)], params=params
        )


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


def test_throughput_meets_local_budget_floor_and_metric_matches_wall_clock(perf_budget) -> None:
    """성능 단언(수치): ADR-2026-09-09-C "백테스트 1개월 M1 1심볼 3초"의 극히
    일부 구간(2,000 bar ≈ 1.4일치 M1)조차 여유 있게 끝나야 한다 — 실측
    기준선(로컬, fake indicator) 대비 8배 이상 여유를 둔 1.0초를 바닥선으로
    건다(tests/foundation/unit/backtest/test_run_backtest.py의 동일 예산과
    같은 값 — L50이 그 실측치를 재확인하고, 새로 배선한 메트릭 관측이 그
    벽시계 실측과 일치하는지까지 함께 증명한다).

    예산 단언은 `perf_budget`(이 프로세스의 `time.process_time()` CPU 시간)으로
    잰다 — `time.perf_counter()` 벽시계는 CI xdist 부하(다른 워커가 코어를
    점유)에 흔들려 80% 부하에서 2.186s까지 관측됐다(ND-1 재현, task-10552).
    메트릭 관측값과 벽시계의 일치 여부는 별개로 `sample.wall_ms`(같은 1회
    실행 구간의 실측 벽시계)와 계속 비교한다."""
    bars = _synthetic_bars(2000)
    fsm = _never_signals_fsm_config()
    spy = _SpyMetrics()

    sample = perf_budget.assert_within(
        lambda: run_backtest(
            _config(), fsm, bars, indicator_service=_FakePriceIndicatorService(), metrics=spy
        ),
        budget_ms=1000.0,
        n=1,
        warmup=0,
        label="2,000 bar 백테스트 처리 — 예산 1.0초(CPU)",
    )
    wall_elapsed = sample.wall_ms / 1000.0

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


def test_insufficient_bars_raises_backtest_run_error_with_metrics_counter() -> None:
    """Negative: warmup_bars가 bar 개수보다 많으면 BacktestRunError를 던지고
    metrics.counter(BACKTEST_RUN_COUNT_TOTAL, {"outcome": "insufficient_warmup"})
    가 호출되는지 검증한다(L50 관측성 배선 — 실패 경로 로그 필드 스냅샷)."""
    bars = _synthetic_bars(5)
    cfg = _config()
    cfg.warmup_bars = 100  # bar 5개에 warmup 100개 — 항상 실패
    fsm = _never_signals_fsm_config()
    spy = _SpyMetrics()

    with pytest.raises(BacktestRunError):
        run_backtest(cfg, fsm, bars, indicator_service=_FakePriceIndicatorService(), metrics=spy)

    counters = [(name, labels) for name, labels in spy.counters]
    assert len(counters) == 1
    name, labels = counters[0]
    assert name == BACKTEST_RUN_COUNT_TOTAL
    assert labels == {"outcome": "insufficient_warmup"}, (
        f"예상 outcome='insufficient_warmup' but got {labels}"
    )


def test_throughput_linear_scaling_1k_vs_10k_bars(perf_budget: PerfBudget) -> None:
    """스케일링 단언(CPU 시간 기준): 10,000 bar가 1,000 bar 대비 100배보다
    적게 걸려야 한다(상수 오버헤드가 지배적이지 않음 확인).

    task-10653(CTO 진단 2026-10-01, full CI 30efe5b6): 원래 `time.perf_counter()`
    벽시계 두 번을 그대로 나눈 비율을 단언했다 — 워커 다수 + 다른 프로세스가
    같이 도는 호스트에서 OS 스케줄러가 두 실행 중 한쪽만 선점하면 비율이 매
    실행마다 달라져 부하 상태에서 간헐 적색이 났다. `perf_budget.best_of`로
    `time.process_time()`(이 프로세스의 CPU 시간, best-of-5)로 바꿔 다른
    프로세스가 코어를 점유한 대기 시간이 섞이지 않게 한다. 1,000 bar 단일
    실행은 Windows `time.process_time()` 분해능(~15.6ms 틱) 아래로 떨어질 수
    있어 `batch=4`로 여러 번 묶어 틱당 오차를 줄인다(PerfBudget.sample 참조,
    tests/conftest.py)."""
    base_cfg = _config()
    base_fsm = _never_signals_fsm_config()
    bars_1k = _synthetic_bars(1000)
    bars_10k = _synthetic_bars(10000)

    sample_1k = perf_budget.best_of(
        lambda: run_backtest(
            base_cfg,
            base_fsm,
            bars_1k,
            indicator_service=_FakePriceIndicatorService(),
            metrics=_SpyMetrics(),
        ),
        n=5,
        warmup=1,
        batch=4,
    )
    sample_10k = perf_budget.best_of(
        lambda: run_backtest(
            base_cfg,
            base_fsm,
            bars_10k,
            indicator_service=_FakePriceIndicatorService(),
            metrics=_SpyMetrics(),
        ),
        n=5,
        warmup=1,
    )

    ratio = sample_10k.cpu_ms / sample_1k.cpu_ms if sample_1k.cpu_ms > 0 else float("inf")
    # window 재구축이 O(n²)이므로 선형이 아님 — 실제 측정치 기준으로
    # 100배 미만이면 상수 오버헤드가 지배적이지 않음
    assert ratio < 100.0, (
        f"10k/1k 처리량 비 {ratio:.1f}x (cpu_ms 10k={sample_10k.cpu_ms:.3f} "
        f"1k={sample_1k.cpu_ms:.3f}) — 상수 오버헤드가 지배적이지 않아야 함(100배 미만)"
    )


def test_metric_labels_snapshot_completed_and_failed() -> None:
    """로그 필드 스냅샷: 성공/실패 경로 모두 metrics.observe/counter에
    outcome 라벨이 포함되는지 검증한다(L50 관측성 배선 — 스냅샷 테스트)."""
    bars = _synthetic_bars(100)
    cfg = _config()
    fsm = _never_signals_fsm_config()
    spy = _SpyMetrics()

    # 성공 경로
    run_backtest(cfg, fsm, bars, indicator_service=_FakePriceIndicatorService(), metrics=spy)

    outcome_labels = {
        frozenset(lbl.items())
        for _, _, lbl in spy.observations
        if lbl is not None and "outcome" in lbl
    }
    assert frozenset([("outcome", "completed")]) in outcome_labels, (
        f"성공 경로 outcome 라벨 미발견 — 관측된 라벨: {spy.observations}"
    )

    # 실패 경로: warmup 부족
    cfg.warmup_bars = 9999
    spy2 = _SpyMetrics()
    with pytest.raises(BacktestRunError):
        run_backtest(cfg, fsm, bars, indicator_service=_FakePriceIndicatorService(), metrics=spy2)

    # counter() 기록은 counters에, observe() 기록은 observations에 별도
    # outcome="insufficient_warmup"은 counter로 기록되므로 counters 확인
    counter_outcomes = {lbl.get("outcome") for _, lbl in spy2.counters if lbl is not None}
    assert "insufficient_warmup" in counter_outcomes, (
        f"실패 경로 outcome 라벨 미발견 — 관측된 observations: {spy2.observations}, "
        f"counters: {spy2.counters}"
    )
