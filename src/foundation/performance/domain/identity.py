"""회계 항등식 검사 — §3.4:
`gross_pnl - fees - slippage - funding + fx - estimated_tax = net_pnl`,
`end = start + net_pnl + Σcashflow`.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6.

절대 예외를 던지지 않는다 — 입력 중 하나라도 `None`(미리컨실)이면 `ok=False`
+ `pending_fields`로 "왜 아직 판단할 수 없는지"를 그대로 보고한다. 0으로
대체해 억지로 항등식을 통과시키지 않는다."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from src.foundation.performance.domain.models import Cashflow, CashflowKind, ComponentBreakdown

_BREAKDOWN_FIELDS = (
    "gross_pnl",
    "fees",
    "slippage",
    "funding",
    "fx",
    "estimated_tax",
    "net_pnl",
)


@dataclass(frozen=True)
class IdentityResult:
    ok: bool
    residual: Decimal | None
    pending_fields: tuple[str, ...]


def _signed(cf: Cashflow) -> Decimal:
    return cf.amount if cf.kind == CashflowKind.DEPOSIT else -cf.amount


def check_identity(
    b: ComponentBreakdown,
    *,
    start_value: Decimal,
    end_value: Decimal,
    cashflows: Sequence[Cashflow],
) -> IdentityResult:
    pending = tuple(
        name for name in _BREAKDOWN_FIELDS if getattr(b, name) is None
    )
    if pending:
        return IdentityResult(ok=False, residual=None, pending_fields=pending)

    computed_net = b.gross_pnl - b.fees - b.slippage - b.funding + b.fx - b.estimated_tax  # type: ignore[operator]
    breakdown_residual = computed_net - b.net_pnl  # type: ignore[operator]

    cashflow_net = sum((_signed(cf) for cf in cashflows), Decimal(0))
    expected_end = start_value + b.net_pnl + cashflow_net  # type: ignore[operator]
    valuation_residual = expected_end - end_value

    ok = breakdown_residual == 0 and valuation_residual == 0
    # 절댓값을 더한다(단순 합이 아니다) — 두 잔차가 부호만 반대고 크기가
    # 같으면 단순 합은 0으로 상쇄돼 ok=False인데 residual=0이라는 모순된
    # 신호를 낸다. 절댓값 합은 그 상쇄가 없어 항상 "실제 불일치 크기"를
    # 반영한다.
    residual = abs(breakdown_residual) + abs(valuation_residual)
    return IdentityResult(ok=ok, residual=residual, pending_fields=())
