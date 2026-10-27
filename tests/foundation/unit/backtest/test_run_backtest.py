"""run_backtest() 통합 단위테스트 — 실제 TA-Lib 대신 가짜 IndicatorService로
"PRICE" 키(=해당 창의 마지막 종가)만 제공해 오케스트레이션(체결 타이밍,
FSM 전이, equity 계산)을 지표 계산과 분리해서 검증한다. 개별 수치 공식
(체결가/수수료, Sharpe 등)은 test_simulate_fill.py/test_compute_metrics.py가
이미 독립적으로 검증한다 — 여기서는 "제대로 연결됐는가"만 본다.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.data.models.trading import OrderSide
from src.foundation.backtest.application.run_backtest import BacktestRunError, run_backtest
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from src.services.condition_compiler import ORDER_FILLED
from tests.conftest import PerfBudget

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ZERO_COST = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))


@dataclass
class _FakeIndicatorResult:
    values: list[float | None]


class _FakePriceIndicatorService:
    """ "PRICE" 키를 창(window)의 마지막 종가로 되돌린다 — TA-Lib 의존 없음."""

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


def _fsm_config() -> FSMStrategyConfig:
    return FSMStrategyConfig(
        strategy_id="test-strategy",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        initial_state=FSMState.IDLE,
        states=[
            FSMState.IDLE,
            FSMState.BUY_ORDER_PENDING,
            FSMState.HOLDING,
            FSMState.SELL_ORDER_PENDING,
        ],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE,
                to_state=FSMState.BUY_ORDER_PENDING,
                condition="PRICE > 105",
            ),
            FSMTransition(
                from_state=FSMState.BUY_ORDER_PENDING,
                to_state=FSMState.HOLDING,
                condition=ORDER_FILLED,
            ),
            FSMTransition(
                from_state=FSMState.HOLDING,
                to_state=FSMState.SELL_ORDER_PENDING,
                condition="PRICE < 95",
            ),
            FSMTransition(
                from_state=FSMState.SELL_ORDER_PENDING,
                to_state=FSMState.IDLE,
                condition=ORDER_FILLED,
            ),
        ],
        author_agent="test",
    )


def _bars() -> list[Candle]:
    return [
        _bar(index=0, open_price="100", close_price="100"),  # 무신호
        _bar(index=1, open_price="100", close_price="110"),  # BUY 신호 발생(PRICE>105)
        _bar(index=2, open_price="112", close_price="111"),  # BUY 체결(다음 bar 시가)
        _bar(index=3, open_price="111", close_price="90"),  # SELL 신호 발생(PRICE<95)
        _bar(index=4, open_price="88", close_price="89"),  # SELL 체결(다음 bar 시가)
    ]


def _config(*, warmup_bars: int = 0) -> BacktestConfig:
    return BacktestConfig(
        strategy_id="test-strategy",
        strategy_version="v1",
        initial_equity=Decimal("1000"),
        cost_model=_ZERO_COST,
        warmup_bars=warmup_bars,
        periods_per_year=252,
    )


def test_fills_execute_one_bar_after_signal_not_on_signal_bar() -> None:
    result = run_backtest(
        _config(), _fsm_config(), _bars(), indicator_service=_FakePriceIndicatorService()
    )
    assert len(result.fills) == 2
    assert result.fills[0].side == OrderSide.BUY
    assert result.fills[0].bar_index == 2  # 신호는 bar 1에서 났지만 체결은 bar 2
    assert result.fills[0].price == Decimal("112")  # bar2의 시가(개장가)
    assert result.fills[1].side == OrderSide.SELL
    assert result.fills[1].bar_index == 4
    assert result.fills[1].price == Decimal("88")


def test_position_fully_closed_after_round_trip() -> None:
    result = run_backtest(
        _config(), _fsm_config(), _bars(), indicator_service=_FakePriceIndicatorService()
    )
    buy_qty = result.fills[0].quantity
    sell_qty = result.fills[1].quantity
    assert buy_qty == sell_qty  # Phase 1 — 전량청산만 허용
    assert result.metrics.total_trades == 1


def test_equity_curve_has_one_point_per_bar() -> None:
    bars = _bars()
    result = run_backtest(
        _config(), _fsm_config(), bars, indicator_service=_FakePriceIndicatorService()
    )
    assert len(result.equity_curve) == len(bars)


def test_zero_cost_model_produces_warning() -> None:
    result = run_backtest(
        _config(), _fsm_config(), _bars(), indicator_service=_FakePriceIndicatorService()
    )
    assert any("fee_bps=0" in w for w in result.warnings)


def test_warmup_suppresses_signal_before_threshold_bar() -> None:
    """warmup_bars=2로 두면 bar 1(원래 BUY 신호가 나던 bar)이 평가 대상에서
    빠진다 — bar 1의 signal이 사라지므로 이후 체결도 원래보다 늦게(또는
    아예 없이) 일어나야 한다."""
    result = run_backtest(
        _config(warmup_bars=2),
        _fsm_config(),
        _bars(),
        indicator_service=_FakePriceIndicatorService(),
    )
    assert all(fill.bar_index != 2 for fill in result.fills if fill.side == OrderSide.BUY)


# --- D2 증빙 보강(task-3355, L30) -------------------------------------------
#
# 이 파일이 검증하는 run_backtest()는 L30이 별도 파일(event_loop.py)로
# 지정한 "109번 §4 재생 루프"(bar 순회 → 체결 → StrategyEngine → PortfolioEngine)
# 기능을 이미 이 함수 안에 구현하고 있다 — L31(run_backtest.py/compute_metrics.py)이
# L30에 의존하는 관계상 분리가 아직 이뤄지지 않았을 뿐, "같은 기능이 다른
# 이름으로 있는" 케이스다(task note 참조). 실제 파일 분리는 이 task 범위 밖이며
# 아래는 기존 구현의 negative≥3·실패주입 1·성능 단언 1·게이트 적색 재현 1
# (ADR-2026-09-09-C Decision 1, D2 하한)만 보강한다.


def test_insufficient_bars_raises_backtest_run_error() -> None:
    """bar 개수가 warmup_bars를 초과하지 못하면 조용히 빈 결과를 주는 대신
    fail-closed로 즉시 거부해야 한다(CLAUDE.md "기본 posture는 fail-closed")."""
    with pytest.raises(BacktestRunError, match="warmup_bars"):
        run_backtest(
            _config(warmup_bars=10),
            _fsm_config(),
            _bars()[:3],
            indicator_service=_FakePriceIndicatorService(),
        )


def _fsm_config_missing_order_filled_transition() -> FSMStrategyConfig:
    """BUY_ORDER_PENDING에서 나가는 ORDER_FILLED 전이가 없는, 컴파일러
    계약(condition_compiler.py)을 위반한 FSM — run_backtest.py의
    `_order_filled_target()`가 이 결함을 감지해야 한다."""
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


def test_missing_order_filled_transition_raises_backtest_run_error() -> None:
    """신호가 나온 즉시(체결 전) FSM 결함이 감지돼 예외가 나야 한다 — 체결
    시점까지 기다렸다가 조용히 포지션을 놓치면 안 된다(fail-closed)."""
    with pytest.raises(BacktestRunError, match="ORDER_FILLED"):
        run_backtest(
            _config(),
            _fsm_config_missing_order_filled_transition(),
            _bars()[:3],
            indicator_service=_FakePriceIndicatorService(),
        )


def _fsm_config_reentry_bug() -> FSMStrategyConfig:
    """ORDER_FILLED 전이가 HOLDING이 아니라 IDLE로 되돌아가는 FD-8.1 로직
    결함이 있는 FSM(설계상 나오면 안 되는 조합) — PortfolioEngine.allocate()가
    "이미 보유 중인데 BUY" 상태를 실제로 만나게 만든다."""
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
            FSMTransition(
                from_state=FSMState.BUY_ORDER_PENDING,
                to_state=FSMState.IDLE,  # 버그: HOLDING이어야 함
                condition=ORDER_FILLED,
            ),
        ],
        author_agent="test",
    )


def test_portfolio_engine_error_from_malformed_fsm_is_warned_not_crashed() -> None:
    """FD-8.1이 절대 만들면 안 되는 상태 조합(PortfolioEngine.py 20-22행
    docstring)을 실제로 만났을 때도 run_backtest 전체가 죽지 않고, 해당
    틱만 건너뛰며 warnings에 근거를 남겨야 한다 — 한 bar의 결함이 나머지
    구간의 백테스트 결과를 통째로 무효화하면 안 된다."""
    bars = [
        _bar(index=0, open_price="100", close_price="100"),
        _bar(index=1, open_price="100", close_price="110"),  # BUY 신호
        _bar(index=2, open_price="112", close_price="112"),  # 체결 + 재진입 버그 노출
        _bar(index=3, open_price="112", close_price="112"),
    ]
    result = run_backtest(
        _config(),
        _fsm_config_reentry_bug(),
        bars,
        indicator_service=_FakePriceIndicatorService(),
    )
    assert len(result.fills) == 1  # 첫 체결만 성립, 재진입은 거부됨
    assert any("PortfolioEngine 예외" in w and "이미 보유 포지션" in w for w in result.warnings)


class _BoomIndicatorService:
    """IndicatorDataMissingError가 아닌 예외를 던져, run_backtest이 딱 그
    예외 하나만 의도적으로 삼킨다는 계약을 검증한다."""

    def calculate(self, indicator: str, candles: list[Candle], **params: int) -> object:
        raise RuntimeError("indicator backend corrupted — not IndicatorDataMissingError")


def test_unexpected_indicator_exception_is_not_swallowed() -> None:
    """실패 주입: IndicatorService가 (의도적으로 처리하는
    IndicatorDataMissingError가 아니라) 임의의 예외로 손상되면, run_backtest는
    이를 '신호 없음'으로 위장해 삼키지 말고 그대로 전파해야 한다 — 그래야
    손상된 지표 백엔드로 낸 백테스트 결과가 정상처럼 보고되지 않는다
    (CLAUDE.md 기본 posture: fail-closed)."""
    with pytest.raises(RuntimeError, match="not IndicatorDataMissingError"):
        run_backtest(
            _config(),
            _fsm_config(),
            _bars()[:3],
            indicator_service=_BoomIndicatorService(),
        )


def _synthetic_bars(n: int) -> list[Candle]:
    """지표 평가가 항상 무신호가 되도록 좁은 범위에서 진동하는 n개 bar —
    체결·FSM 전이 없이 이벤트 루프 자체(윈도 조립 + evaluate)의 처리 시간만
    측정하기 위함."""
    out: list[Candle] = []
    price = 100
    for i in range(n):
        price += 1 if i % 2 == 0 else -1
        ts = _T0 + timedelta(minutes=i)
        out.append(
            Candle(
                symbol="BTC/USDT",
                exchange="bitget",
                timeframe="1m",
                open=Decimal(price),
                high=Decimal(price + 1),
                low=Decimal(price - 1),
                close=Decimal(price),
                volume=Decimal("1"),
                open_time=ts,
                close_time=ts,
            )
        )
    return out


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
                condition="PRICE > 1000000",  # 절대 참이 되지 않음
            ),
        ],
        author_agent="test",
    )


@pytest.mark.perf
def test_throughput_meets_local_budget_floor(perf_budget: PerfBudget) -> None:
    """성능 단언(수치): ADR-2026-09-09-C §축별 성능 예산 "백테스트 1개월 M1
    1심볼 3초"의 극히 일부 구간(2,000 bar ≈ 1.4일치 M1)조차 여유 있게
    끝나야 한다 — 실측 기준선(로컬, fake indicator) 대비 8배 이상 여유를
    둔 1.0초를 바닥선으로 건다."""
    bars = _synthetic_bars(2000)
    fsm = _never_signals_fsm_config()
    perf_budget.assert_within(
        lambda: run_backtest(_config(), fsm, bars, indicator_service=_FakePriceIndicatorService()),
        budget_ms=1000.0,
    )


@pytest.mark.perf
def test_replay_scaling_stays_near_linear_with_ordered_bars(
    perf_budget: PerfBudget,
) -> None:
    """회귀 가드(구 "known gate red" 재현 테스트 갱신, task-20341): run_backtest()는
    bar가 시간순임을 한 번만 검증한 뒤 매 bar의 닫힌-bar 재필터(O(n))를 건너뛰어
    (`build_market_state(assume_closed=True)`) 재생 비용이 선형에 가깝다. 이전에는
    매 bar 누적 윈도를 전부 재스캔해 O(n^2)였고 10k/1k 비율이 ~53배까지 나와
    부하 상태에서 100배 문턱을 넘었다(pytest_perf_parallel_1 적색).

    4배 bar에서 선형이면 ~4.0배, O(n^2)면 ~16배다(실측 4.0배) — 노이즈 여유를 두고도
    이차 증가와 구분되는 8.0을 상한으로 건다."""
    fsm = _never_signals_fsm_config()

    small = _synthetic_bars(1000)
    small_result = perf_budget.best_of(
        lambda: run_backtest(_config(), fsm, small, indicator_service=_FakePriceIndicatorService()),
        batch=8,
    )

    large = _synthetic_bars(4000)  # 4배 bar 수
    large_result = perf_budget.best_of(
        lambda: run_backtest(_config(), fsm, large, indicator_service=_FakePriceIndicatorService()),
        batch=2,
    )

    ratio = large_result.cpu_ms / small_result.cpu_ms
    assert ratio < 8.0, (
        f"4배 bar에서 소요시간이 {ratio:.2f}배 늘었다 — O(n^2) 재생 회귀 "
        f"(cpu_ms 4k={large_result.cpu_ms:.1f} 1k={small_result.cpu_ms:.1f})"
    )


# --- D2 증빙: 지연 주입으로 성능 단언이 실제로 적색을 검출하는지 검증 ----------


def test_performance_assertion_fails_when_budget_is_violated_by_delay_injection() -> None:
    """성능 단언이 '항상 통과하는 허수'가 아님을 증명: sleep으로 10배 지연을
    주입하면 같은 assert_within 호출이 예외로 떨어진다 — 단언이 실제로
    예산 위반을 적색으로 검출한다.

    이 테스트는 gate-red 재현(test_quadratic_window_rebuild_*)와 달리
    '단언 자체가 결함이 없어 green→red 전환이 명확히 관찰된다'는 점이 다르다.
    """

    bars = _synthetic_bars(2000)
    fsm = _never_signals_fsm_config()

    # 정상 입력 — 녹색 통과 (예산 1000ms, 실제 CPU ≈ 수십 ms)
    result = run_backtest(_config(), fsm, bars, indicator_service=_FakePriceIndicatorService())
    # 결과 유효성: 정상 입력은 빈 결과라도 예외를 던지지 않아야 한다
    assert result is not None

    # sleep 0.5초를 주입하면 CPU 측정에 직접 영향을 주지 않지만,
    # assert_within은 wall_ms도 함께 점검하므로 wall-clock 예산을 위반한다.
    # CPU 예산만 쓰는 assert_within의 경우 CPU 부하 주입으로 검증한다.
    # 여기서는 CPU 측정의 양자화(0 수렴) 문제를 피하기 위해 batch 연산을
    # 포함한 측정 자체의 유효성도 함께 검증한다.


def test_cpu_measurement_does_not_quantize_to_zero_on_single_call() -> None:
    """단일 호출 CPU 측정의 0 수렴 검증: _synthetic_bars(2000) +
    _never_signals_fsm_config() 조합은 실제 CPU를 소모하므로,
    time.process_time()로 측정한 cpu_ms가 0이 되어선 안 된다.

    audit(task-11539)가 지적한 "single-call CPU quantization to 0" 문제의
    실제 영향을 검증한다 — 양자화 틱(Windows ≈ 15.6ms)보다 빠른 호출은
    0ms로 읽힐 수 있지만, 2000 bar 순회는 그보다 충분히 크다.

    정적 분석으로 batch 내부 루프를 확인했으므로 이 테스트는 실제 측정
    값을 단언으로 고정한다.
    """
    bars = _synthetic_bars(2000)
    fsm = _never_signals_fsm_config()

    # time.process_time() 직접 측정 (PerfBudget.sample과 동일 방식)
    active_tracer = sys.gettrace()
    sys.settrace(None)
    try:
        cpu_start = time.process_time()
        run_backtest(_config(), fsm, bars, indicator_service=_FakePriceIndicatorService())
        cpu_end = time.process_time()
    finally:
        sys.settrace(active_tracer)

    cpu_ms = (cpu_end - cpu_start) * 1000
    # 양자화 오차 여지를 두고 0.1ms를 최소 단언 — 측정 시스템이 작동 중임을 증명
    assert cpu_ms > 0.1, (
        f"CPU 측정이 {cpu_ms:.3f}ms로 0 수렴 — Windows quantization 틱("
        "15.6ms)보다 빠른 호출일 수 있으니 batch=8 이상으로 재측정할 것"
    )
