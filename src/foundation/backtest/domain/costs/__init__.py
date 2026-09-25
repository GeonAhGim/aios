"""BT-8 — 백테스트 비용 2종(펀딩·차입) 패키지.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-8(`domain/costs/{funding,borrow}.py`), §3.4(`BacktestConfigV2.costs`),
§9.5 BT-8(DoD: "일할 계산 정확").

`funding.py`(고정 인터벌 정산)와 `borrow.py`(일할 계산)는 정산 방식이
달라 파일을 나눴지만, 최종 비용의 반올림 규칙만은 이 `round_cost` 하나로
고정한다 — 두 파일이 각자 반올림을 따로 구현하면 같은 입력에도 마지막
자리가 어긋날 수 있다.

DEEPEN task-3039: `exact_total_seconds` also lives here for the same reason.
`timedelta.total_seconds()` returns a `float`, silently breaking this
package's "day-count math stays in Decimal" invariant — for offsets far
from the epoch (e.g. year 2300+) the float division loses precision below
1e-8s, and near a settlement boundary that error could flip the ceiling
division that counts funding settlements.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal

_COST_QUANTIZE_EXPONENT = Decimal("0.00000001")  # 1e-8(소수 8자리) — 거래소 통화 정밀도 상한 관례
_SECONDS_PER_DAY = Decimal(86400)
_MICROSECONDS_PER_SECOND = Decimal(1_000_000)


def round_cost(value: Decimal) -> Decimal:
    """비용 계산 결과를 소수 8자리로 반올림(HALF_EVEN, 은행가 반올림)한다.

    `funding.py`·`borrow.py`가 최종 반환 직전 이 함수 하나만 거치게 해
    반올림 로직 중복 구현을 막는다.
    """

    return value.quantize(_COST_QUANTIZE_EXPONENT, rounding=ROUND_HALF_EVEN)


def exact_total_seconds(delta: timedelta) -> Decimal:
    """Convert a `timedelta` to an exact `Decimal` count of seconds.

    `delta.total_seconds()` returns a `float`; this instead combines the
    three integer fields (`days`, `seconds`, `microseconds`) directly into a
    `Decimal`, bypassing the float division entirely. `delta` must be
    non-negative (callers already enforce `exit >= entry`).
    """

    return (
        Decimal(delta.days) * _SECONDS_PER_DAY
        + Decimal(delta.seconds)
        + Decimal(delta.microseconds) / _MICROSECONDS_PER_SECOND
    )
