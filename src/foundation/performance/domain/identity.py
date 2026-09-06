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


def _require(value: Decimal | None, field: str) -> Decimal:
    """pending 필드 검사 이후에도 `None`이면 호출자의 불변식 위반 —
    조용히 0으로 대체하지 않고 예외로 드러낸다."""
    if value is None:
        raise ValueError(f"{field}가 None (pending_fields 검사를 통과했어야 함)")
    return value


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

    gross_pnl = _require(b.gross_pnl, "gross_pnl")
    fees = _require(b.fees, "fees")
    slippage = _require(b.slippage, "slippage")
    funding = _require(b.funding, "funding")
    fx = _require(b.fx, "fx")
    estimated_tax = _require(b.estimated_tax, "estimated_tax")
    net_pnl = _require(b.net_pnl, "net_pnl")

    computed_net = gross_pnl - fees - slippage - funding + fx - estimated_tax
    breakdown_residual = computed_net - net_pnl

    cashflow_net = sum((_signed(cf) for cf in cashflows), Decimal(0))
    expected_end = start_value + net_pnl + cashflow_net
    valuation_residual = expected_end - end_value

    ok = breakdown_residual == 0 and valuation_residual == 0
    # 절댓값을 더한다(단순 합이 아니다) — 두 잔차가 부호만 반대고 크기가
    # 같으면 단순 합은 0으로 상쇄돼 ok=False인데 residual=0이라는 모순된
    # 신호를 낸다. 절댓값 합은 그 상쇄가 없어 항상 "실제 불일치 크기"를
    # 반영한다.
    residual = abs(breakdown_residual) + abs(valuation_residual)
    return IdentityResult(ok=ok, residual=residual, pending_fields=())
