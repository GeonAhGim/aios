"""16.1 — Capital allocation upper-bound enforcement.

Spec: 기능설계문서_v1.20.md#FD-16.1, Policy docs 8.2-B, 9.1

The applicable cap depends on the strategy's certification status (certified_badge):
unverified strategies (self-built or uncertified purchases, including FD-14 editor outputs)
are limited to unverified_max_pct (Draft 10%), while strategies that pass 9.5-A certification
(certified_badge=true) may allocate up to certified_level4_max_pct (Draft 25%).
Allocation requests exceeding the cap are rejected at storage — the UI cannot bypass the
FROZEN Risk policy. The error reports the exact overage and current cap
(FD-1.2 "Do not make users guess" principle).
"""
from __future__ import annotations

from decimal import Decimal

from src.core.loader.risk_policy_loader import StrategyAllocationPolicy


class CapitalAllocationError(Exception):
    """FD-16.1 Storage rejected — router translates to 400."""


def allocation_cap_pct(certified_badge: bool, policy: StrategyAllocationPolicy) -> Decimal:
    pct = policy.certified_level4_max_pct if certified_badge else policy.unverified_max_pct
    return Decimal(str(pct))


def validate_capital_allocation(
    allocated_capital: Decimal,
    available_balance: Decimal,
    *,
    certified_badge: bool,
    policy: StrategyAllocationPolicy,
) -> None:
    if available_balance <= 0:
        raise CapitalAllocationError("사용 가능한 잔고가 없습니다.")
    if allocated_capital <= 0:
        raise CapitalAllocationError("배분 금액은 0보다 커야 합니다.")

    cap_pct = allocation_cap_pct(certified_badge, policy)
    requested_pct = (allocated_capital / available_balance) * Decimal("100")

    if requested_pct > cap_pct:
        cap_amount = (available_balance * cap_pct / Decimal("100")).quantize(Decimal("0.01"))
        excess = (allocated_capital - cap_amount).quantize(Decimal("0.01"))
        raise CapitalAllocationError(
            f"배분 상한 초과 — 현재 상한은 잔고의 {cap_pct}%({cap_amount}), "
            f"요청 금액({allocated_capital})이 {excess}만큼 초과합니다."
        )
