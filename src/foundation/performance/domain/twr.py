"""시간가중수익률(TWR) — `pm-v1` 방법론의 "PERIOD_LINKED_CASHFLOW_AT_START".

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6.

현금흐름이 있는 경계마다 하위기간으로 끊어 기하연결한다(GIPS 표준 TWR,
Modified Dietz 근사가 아니다) — 그래서 각 현금흐름 시각에 정확히 일치하는
평가액(`valuations`)이 있어야 한다. 없으면 근사치를 만드는 대신 명시적으로
거부한다(reconciliation의 "never assume zero"와 같은 태도 — 있는 척하지
않는다).

경계 `t_k`의 평가액은 그 시각에 발생한 현금흐름이 반영되기 **직전** 값으로
받는다(관례) — 하위기간 `[t_{k-1}, t_k]`의 실제 투자원금은
`valuations[t_{k-1}].value + cashflow_at(t_{k-1})`이다("현금흐름을 기초에
반영").

반환값은 비율(0.0523 = 5.23%)이지 백분율 숫자가 아니다 — `%` 표시는
contracts 계층(`ReturnValue.value_pct`)의 몫이다.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from src.foundation.performance.domain.models import Cashflow, CashflowKind


class MissingInputError(Exception):
    """72번 에러 taxonomy `INTEGRITY_STATEMENT_INPUT_UNRECONCILED` — 이 함수가
    필요로 하는 입력(현금흐름 시점 평가액 등)이 없다. 0이나 보간값으로
    메우지 않는다."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"INTEGRITY_STATEMENT_INPUT_UNRECONCILED: {detail}")
        self.reason_code = "INTEGRITY_STATEMENT_INPUT_UNRECONCILED"


def _signed(cf: Cashflow) -> Decimal:
    return cf.amount if cf.kind == CashflowKind.DEPOSIT else -cf.amount


def twr(
    valuations: list[tuple[datetime, Decimal]],
    cashflows: list[Cashflow],
) -> Decimal:
    if len(valuations) < 2:
        raise MissingInputError("twr()에는 최소 2개(기간 시작/끝) 평가액이 필요합니다.")

    ordered = sorted(valuations, key=lambda v: v[0])
    valuation_times = {at for at, _ in ordered}
    cashflow_by_time: dict[datetime, Decimal] = {}
    for cf in cashflows:
        if cf.at not in valuation_times:
            raise MissingInputError(
                f"현금흐름 시각 {cf.at.isoformat()}에 일치하는 평가액이 없습니다."
            )
        cashflow_by_time[cf.at] = cashflow_by_time.get(cf.at, Decimal(0)) + _signed(cf)

    linked = Decimal(1)
    for (prev_at, prev_value), (_cur_at, cur_value) in zip(ordered, ordered[1:], strict=False):
        base = prev_value + cashflow_by_time.get(prev_at, Decimal(0))
        if base == 0:
            raise MissingInputError(
                f"{prev_at.isoformat()} 하위기간의 투자원금이 0이라 수익률을 정의할 수 없습니다."
            )
        subperiod_return = cur_value / base - 1
        linked *= Decimal(1) + subperiod_return

    return linked - 1
