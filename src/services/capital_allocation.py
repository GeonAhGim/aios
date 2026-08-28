"""16.1 — 자본 배분 상한 검증.

Spec: 기능설계문서_v1.20.md#FD-16.1, 정책문서 8.2-B, 9.1

전략의 인증 상태(certified_badge)에 따라 적용 상한이 다르다 — 미인증
(직접 제작 또는 미인증 구매 전략, FD-14 편집기 산출물 포함)은
unverified_max_pct(Draft 10%), 9.5-A 인증 통과(certified_badge=true)는
certified_level4_max_pct(Draft 25%). 초과 시 저장 자체를 거부한다
(FROZEN Risk 정책을 UI가 우회하지 않음) — 정확한 초과분과 현재 상한을
함께 안내한다(FD-1.2 "추측하게 하지 않는다" 원칙).
"""
from __future__ import annotations

from decimal import Decimal

from src.core.loader.risk_policy_loader import StrategyAllocationPolicy


class CapitalAllocationError(Exception):
    """FD-16.1 저장 거부 — 라우터가 400으로 변환."""


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
