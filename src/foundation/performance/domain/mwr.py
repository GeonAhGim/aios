"""금액가중수익률(MWR) — IRR을 이분법(bisection)으로 결정론적으로 푼다.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6, methodology.py
(`MWR_MAX_ITERATIONS=200`, `MWR_TOLERANCE=1e-10`).

투자자 관점의 현금흐름열로 바꾼다 — 기간 시작의 `start_value`는 투자자가
"넣은" 돈(유출), 기간 중 입금은 추가 유출, 출금은 유입, 기간 끝의
`end_value`는 투자자가 "찾을 수 있는" 돈(유입)이다. 이 열의 순현재가치가
0이 되는 할인율 r이 IRR이다. 시간축은 [0,1](기간 전체를 1로 정규화)로
잡는다 — 연환산은 호출부(risk_metrics.py 소비자)의 몫이다.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from src.foundation.performance.domain.methodology import MWR_MAX_ITERATIONS, MWR_TOLERANCE
from src.foundation.performance.domain.models import Cashflow, CashflowKind

_LOW_RATE = Decimal("-0.999999")
_HIGH_RATE = Decimal(10)


def _signed(cf: Cashflow) -> Decimal:
    return cf.amount if cf.kind == CashflowKind.DEPOSIT else -cf.amount


def _npv(rate: Decimal, flows: Sequence[tuple[Decimal, Decimal]]) -> Decimal:
    total = Decimal(0)
    base = Decimal(1) + rate
    for t, amount in flows:
        discount = base**t if t != 0 else Decimal(1)
        total += amount / discount
    return total


def mwr(
    cashflows: Sequence[Cashflow],
    start_value: Decimal,
    end_value: Decimal,
    start: datetime,
    end: datetime,
) -> Decimal | None:
    total_seconds = (end - start).total_seconds()
    if total_seconds <= 0 or start_value <= 0:
        return None

    flows: list[tuple[Decimal, Decimal]] = [(Decimal(0), -start_value)]
    for cf in cashflows:
        elapsed = (cf.at - start).total_seconds()
        if elapsed < 0 or elapsed > total_seconds:
            continue  # 기간 밖 현금흐름은 호출부 책임 — 방어적으로 무시
        t = Decimal(elapsed) / Decimal(total_seconds)
        flows.append((t, -_signed(cf)))
    flows.append((Decimal(1), end_value))

    low, high = _LOW_RATE, _HIGH_RATE
    f_low, f_high = _npv(low, flows), _npv(high, flows)
    if f_low == 0:
        return low
    if f_high == 0:
        return high
    if (f_low > 0) == (f_high > 0):
        return None  # 브래킷 안에서 부호가 안 바뀜 — 수렴 실패

    for _ in range(MWR_MAX_ITERATIONS):
        mid = (low + high) / 2
        f_mid = _npv(mid, flows)
        if abs(f_mid) < MWR_TOLERANCE or (high - low) < MWR_TOLERANCE:
            return mid
        if (f_mid > 0) == (f_low > 0):
            low, f_low = mid, f_mid
        else:
            high = mid
    return None  # MWR_MAX_ITERATIONS 안에 수렴하지 못함
