"""즉시 백테스트 벤치 — ADR-2026-09-09-C Decision 1 "1개월 M1 즉시 백테스트 ≤5s" 예산의
1년 일봉·1종목 변형을 재현한다(§7 "1개월 기준"은 M1 상한 얘기고, RD-6 명세 요구 값은
"1년 일봉 1종목"이므로 그 조합으로 측정).

`run_quick_backtest`(BT-10, 컬럼 경로·순수·no I/O)에 인메모리로 만든 1년치(365봉)
일봉 `CandleColumns`를 먹여 전체 실행(봉 순회+체결+결과 조립) 벽시계를 N회 반복
측정하고 `docs/perf/backtest_instant_bench.json`에 쓴다. 캔들 입력 구성은
`tests/foundation/unit/backtest/test_quick_backtest.py`의 `_columns`/`_config`
패턴을 그대로 따른다 — 실DB 컬럼 경로(LA-23b `read_candles_columnar` 왕복)는
`tests/foundation/integration/backtest/test_quick_backtest.py`가 별도로 잰다.

사용: `python scripts/bench/backtest_instant_bench.py [--out PATH] [--iterations N]`.
DB/네트워크 불필요. 종료코드는 항상 0 — 예산 초과는 `passed=false`로 기록된다.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.bench._common import ROOT, summarize, write_result  # noqa: E402
from src.data.models.trading import OrderSide  # noqa: E402
from src.foundation.backtest.application.quick_backtest import (  # noqa: E402
    BarWindow,
    OrderIntent,
    PositionState,
    run_quick_backtest,
)
from src.foundation.backtest.domain.models_v2 import (  # noqa: E402
    AdjustmentsConfig,
    BacktestConfigV2,
    CostsConfig,
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    VenueTierCommission,
)
from src.foundation.market_data.contracts.v1 import Timeframe  # noqa: E402
from src.foundation.market_data.domain.candle_columns import CandleColumns  # noqa: E402

BUDGET_MS = 5_000.0
BARS_PER_YEAR = 365
_T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
_CASH = Decimal("100000")
_D = Decimal
DEFAULT_OUT = ROOT / "docs" / "perf" / "backtest_instant_bench.json"


def _one_year_daily_columns(seed: int = 0) -> CandleColumns:
    """결정론적 유사난수 워크 — 매 실행 동일 입력이라야 벤치가 재현 가능하다."""
    closes: list[Decimal] = []
    price = 100.0
    state = seed or 1
    for _ in range(BARS_PER_YEAR):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        drift = ((state % 2001) - 1000) / 10_000.0  # ~[-0.10, +0.10]
        price = max(1.0, price * (1 + drift))
        closes.append(_D(str(round(price, 2))))
    opens = [closes[0], *closes[:-1]]
    return CandleColumns(
        ts=[_T0 + timedelta(days=i) for i in range(BARS_PER_YEAR)],
        open=opens,
        high=[max(o, c) + 1 for o, c in zip(opens, closes, strict=True)],
        low=[min(o, c) - 1 for o, c in zip(opens, closes, strict=True)],
        close=closes,
        volume=[_D("1000")] * BARS_PER_YEAR,
        quote_volume=[None] * BARS_PER_YEAR,
    )


def _config() -> BacktestConfigV2:
    return BacktestConfigV2(
        slippage=FixedSlippage(bps=_D("10")),
        commission=VenueTierCommission(
            venue="BITGET", maker_bps=_D("2"), taker_bps=_D("5"), min_fee=_D("0")
        ),
        latency_ms=0,
        partial_fill=PartialFillConfig(max_participation_pct=_D("1")),
        order_types=OrderTypesConfig(limit=True, stop=True, oco=True, trailing=True),
        magnifier_tf=None,
        costs=CostsConfig(funding=False, borrow_apr=None),
        adjustments=AdjustmentsConfig(splits=False, dividends=False),
        calendar="24x7",
    )


class _SmaCrossStrategy:
    """대표적인 실전 전략의 최소 재현 — 매 봉 짧은/긴 SMA 교차로 시장가 주문 여부를 결정한다.

    측정 목적상 지표는 창(window)에서 그때그때 계산한다(엔진이 지표 캐싱을 강제하지
    않는 한 실제 전략 콜백이 물리는 비용의 하한을 대표한다).
    """

    def __init__(self, fast: int = 10, slow: int = 30) -> None:
        self.fast = fast
        self.slow = slow

    def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None:
        if len(window) < self.slow + 2:
            return None
        fast_ma = sum(window.close(-k) for k in range(1, self.fast + 1)) / self.fast
        slow_ma = sum(window.close(-k) for k in range(1, self.slow + 1)) / self.slow
        prev_fast = sum(window.close(-k) for k in range(2, self.fast + 2)) / self.fast
        prev_slow = sum(window.close(-k) for k in range(2, self.slow + 2)) / self.slow
        if fast_ma > slow_ma and prev_fast <= prev_slow and position.quantity == 0:
            return OrderIntent(side=OrderSide.BUY, quantity=_D("1"))
        if fast_ma < slow_ma and prev_fast >= prev_slow and position.quantity != 0:
            return OrderIntent(side=OrderSide.SELL, quantity=position.quantity)
        return None


def _run_once() -> float:
    config, columns = _config(), _one_year_daily_columns()
    started = time.perf_counter()
    run_quick_backtest(
        config,
        columns,
        timeframe=Timeframe.D1,
        strategy=_SmaCrossStrategy(),
        initial_cash=_CASH,
    )
    return (time.perf_counter() - started) * 1000


def run(iterations: int) -> list[float]:
    return [_run_once() for _ in range(iterations)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args(argv)

    samples = run(args.iterations)
    result = summarize(
        "backtest_instant_bench",
        samples,
        BUDGET_MS,
        note=f"1년 일봉({BARS_PER_YEAR}봉) 1종목 인메모리 컬럼 경로",
    )
    write_result(result, args.out)

    status = "PASS" if result.passed else "FAIL"
    print(
        f"[backtest_instant_bench] {status} p50={result.p50_ms:.2f}ms p95={result.p95_ms:.2f}ms "
        f"max={result.max_ms:.2f}ms budget<{BUDGET_MS:.0f}ms n={result.samples} -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
