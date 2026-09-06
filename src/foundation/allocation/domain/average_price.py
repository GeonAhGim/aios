"""FA-7 — allocation/domain/average_price.py: 블록 주문 누적 평균단가(순수).

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-7 (§1 "블록 주문 배분"·§4 FA-A3).

블록 주문은 여러 번 부분체결되고 각 체결의 가격이 다를 수 있다. 이 모듈은
그 부분체결들을 하나의 가중평균 단가로 뭉치고, `policy.py`가 만든
`AllocationLine`(정책·라운딩과 무관)에 그 단일 단가를 부여한다 — 정책이
먼저 수량을 정하고, 이 모듈이 가격을 붙이는 순서다. 모든 sub_account가
같은 평균단가를 받아야 실행 타이밍에 따른 형평성 문제가 생기지 않는다
(§1 "여러 sub_account에 평균단가로 배분").

FA-A3 "평균단가 가중합 오차 ≤ 1 최소단위"는 `apply_average_price`가
검증한다: 배분된 (수량 × 평균단가)의 합과 원 체결의 총 명목가치
(`Σ quantity × price`) 차이가 `notional_quantum`을 넘으면
`AllocationResidualError`다 — 정책의 수량 라운딩이 가격 쪽으로 전이되는
오차가 있다면 여기서 잡힌다. Decimal 무손실(quantize는 최종 평균단가
계산에서만 1회, float 금지).
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from src.foundation.allocation.domain.policy import (
    AllocationLine,
    AllocationResidualError,
    round_to_quantum,
)

SCHEMA_VERSION: Literal["v1"] = "v1"


class PartialFill(BaseModel):
    """블록 주문의 부분체결 한 건. 양수 검증은 `blended_average_price`의
    책임이다([[policy.WeightTarget]]과 동일 이유 — 계약은 형태만)."""

    quantity: Decimal
    price: Decimal
    schema_version: Literal["v1"] = SCHEMA_VERSION


class SubAccountAllocation(BaseModel):
    """평균단가가 부여된 sub_account별 배분 결과."""

    sub_account_id: UUID
    quantity: Decimal
    average_price: Decimal
    schema_version: Literal["v1"] = SCHEMA_VERSION


def blended_average_price(fills: Sequence[PartialFill], price_quantum: Decimal) -> Decimal:
    """부분체결들의 수량가중 평균단가. `Σquantity·price / Σquantity`를
    `price_quantum` 단위로 1회 `ROUND_HALF_EVEN` 반올림한다."""
    if not fills:
        raise AllocationResidualError("평균단가 계산 대상 체결이 없음")
    if price_quantum <= 0:
        raise AllocationResidualError(f"price_quantum은 0보다 커야 함: {price_quantum}")
    for f in fills:
        if f.quantity <= 0:
            raise AllocationResidualError(f"quantity는 0보다 커야 함: {f.quantity}")
        if f.price <= 0:
            raise AllocationResidualError(f"price는 0보다 커야 함: {f.price}")
    total_quantity = sum((f.quantity for f in fills), Decimal("0"))
    total_notional = sum((f.quantity * f.price for f in fills), Decimal("0"))
    return round_to_quantum(total_notional / total_quantity, price_quantum)


def apply_average_price(
    lines: Sequence[AllocationLine],
    average_price: Decimal,
    *,
    total_notional: Decimal,
    notional_quantum: Decimal,
) -> tuple[SubAccountAllocation, ...]:
    """배분 라인 전부에 같은 `average_price`를 부여하고 FA-A3(가중합 오차
    ≤ 1 최소단위)를 검증한다."""
    if not lines:
        raise AllocationResidualError("평균단가를 부여할 배분 라인이 없음")
    if notional_quantum <= 0:
        raise AllocationResidualError(f"notional_quantum은 0보다 커야 함: {notional_quantum}")

    result = tuple(
        SubAccountAllocation(
            sub_account_id=line.sub_account_id,
            quantity=line.quantity,
            average_price=average_price,
        )
        for line in lines
    )
    weighted_sum = sum((line.quantity * average_price for line in result), Decimal("0"))
    error = abs(weighted_sum - total_notional)
    if error > notional_quantum:
        raise AllocationResidualError(
            f"평균단가 가중합 오차({error})가 최소단위({notional_quantum})를 초과함"
        )
    return result
