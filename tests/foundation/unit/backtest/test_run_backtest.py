"""run_backtest() 통합 단위테스트 — 실제 TA-Lib 대신 가짜 IndicatorService로
"PRICE" 키(=해당 창의 마지막 종가)만 제공해 오케스트레이션(체결 타이밍,
FSM 전이, equity 계산)을 지표 계산과 분리해서 검증한다. 개별 수치 공식
(체결가/수수료, Sharpe 등)은 test_simulate_fill.py/test_compute_metrics.py가
이미 독립적으로 검증한다 — 여기서는 "제대로 연결됐는가"만 본다.
"""

from __future__ import annotations

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


def test_throughput_meets_local_budget_floor() -> None:
    """성능 단언(수치): ADR-2026-09-09-C §축별 성능 예산 "백테스트 1개월 M1
    1심볼 3초"의 극히 일부 구간(2,000 bar ≈ 1.4일치 M1)조차 여유 있게
    끝나야 한다 — 실측 기준선(로컬, fake indicator) 대비 8배 이상 여유를
    둔 1.0초를 바닥선으로 건다."""
    bars = _synthetic_bars(2000)
    fsm = _never_signals_fsm_config()
    start = time.perf_counter()
    run_backtest(_config(), fsm, bars, indicator_service=_FakePriceIndicatorService())
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0


def test_quadratic_window_rebuild_is_a_known_gate_red_against_monthly_budget() -> None:
    """게이트 적색 재현: run_backtest()는 매 bar마다 `bars[: bar_index + 1]`로
    전체 누적 윈도를 새로 만들어 build_market_state에 넘긴다(L28
    series_cache의 점진적 인과 계산을 쓰지 않음) — bar 수 n에 대해 O(n^2)로
    스케일한다.

    ADR-2026-09-09-C 축별 성능 예산 "백테스트 1개월 M1 1심볼 3초"는 1개월치
    1분봉(약 43,200 bar) 기준이다. 아래에서 실측한 4배 스케일 비율(선형이면
    4배, 실측은 그보다 뚜렷이 큼)을 43,200 bar까지 외삽하면 (43200/4000)^2
    ≈ 116.6배 → 3초는커녕 수십 초가 걸려 예산을 위반한다(적색). 근본 수정
    (L28 인과 캐시를 event_loop에 연결)은 이 task의 범위(D2 증빙 보강, 파일
    분리·리팩터 없음)를 벗어나므로, 여기서는 결함을 재현·수치로 고정만 하고
    N/A 처리하지 않는다 — 실제로 해당 실패 모드가 있기 때문이다."""
    fsm = _never_signals_fsm_config()

    small = _synthetic_bars(1000)
    start = time.perf_counter()
    run_backtest(_config(), fsm, small, indicator_service=_FakePriceIndicatorService())
    small_elapsed = time.perf_counter() - start

    large = _synthetic_bars(4000)  # 4배 bar 수
    start = time.perf_counter()
    run_backtest(_config(), fsm, large, indicator_service=_FakePriceIndicatorService())
    large_elapsed = time.perf_counter() - start

    ratio = large_elapsed / small_elapsed
    # 선형 스케일이면 ratio ≈ 4.0. O(n^2)이면 이상적으로는 16.0에 근접해야
    # 하지만 고정 오버헤드가 섞여 실측은 그보다 낮다 — 노이즈 여유를 두고도
    # 선형(4.0)과는 뚜렷이 구분되는 6.0을 문턱으로 건다.
    assert ratio > 6.0, (
        f"4배 bar에서 소요시간이 {ratio:.2f}배만 늘었다 — O(n^2) 재현 실패 "
        "(회귀로 이미 고쳐졌다면 이 테스트를 갱신하고 커밋 메시지에 근거를 남길 것)"
    )
