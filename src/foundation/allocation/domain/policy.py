"""FA-7 — allocation/domain/policy.py: 3정책 배분(순수).

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-7 (§2.2 표·§4 FA-A3).

`pro_rata`(비례) / `fixed_weight`(고정비율) / `manual`(수동 지정) 세 정책 모두
"체결 수량을 여러 sub_account로 쪼개되 합은 항상 원 수량과 정확히 같다"는
하나의 계약(FA-A3)을 만족해야 한다. `pro_rata`와 `fixed_weight`는 계산이
동일하다 — 가중치 벡터의 출처만 다르다(pro_rata: 임의 양수 비율, 정규화해서
사용; fixed_weight: 합이 정확히 1이어야 하는 사전 고정 비율). 두 정책 모두
`_allocate_by_weight`를 공유한다.

라운딩은 [[rounding]](`src/foundation/ledger/domain/rounding.py`, LC-2)의
원칙 — "반올림으로 생기는 나머지를 정해진 규칙으로 몰아줘서 합을 보존한다" —
을 그대로 따르되 그 함수 자체(`split_commission`)는 2-way·고정 KRW quantum
전용이라 이 리프의 N-way·임의 quantum 케이스에는 재사용할 수 없다(재구현이
아니라 같은 원칙을 N-way로 일반화한 것). 각 목표를 quantum 단위로
`ROUND_HALF_EVEN` 반올림한 뒤, 반올림 오차(= 체결 수량 − 반올림 합)를
가중치가 큰 목표부터 순서대로 quantum 단위씩 보정한다 — 상대적으로 가장
왜곡이 작은 목표부터 흡수시키기 위함이다. 순수(I/O·DB 임포트 0).
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum
from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel

SCHEMA_VERSION: Literal["v1"] = "v1"


class AllocationErrorCode(str, Enum):
    """§3 에러 taxonomy 중 이 리프(FA-7)가 정의하는 배분 관련 코드."""

    RESIDUAL = "FA_ALLOCATION_RESIDUAL"  # 409, 배분 잔여/입력 불일치


class AllocationResidualError(ValueError):
    """FA_ALLOCATION_RESIDUAL(409) — 배분 합이 체결 수량과 어긋나거나 입력이
    배분 불가능한 상태(가중치 합 불일치, 음수/0 수량, 미지 정책 등)."""

    code: ClassVar[AllocationErrorCode] = AllocationErrorCode.RESIDUAL


class AllocationPolicy(str, Enum):
    PRO_RATA = "pro_rata"
    FIXED_WEIGHT = "fixed_weight"
    MANUAL = "manual"


class WeightTarget(BaseModel):
    """`pro_rata`/`fixed_weight` 배분 대상 한 개. 양수 검증은 이 계약이
    아니라 배분 함수(`_allocate_by_weight`)의 책임이다(FA-1 `contracts/v1.py`
    관례 — 계약은 형태만, 불변조건은 domain 함수) — pydantic
    `model_validator`가 `ValueError`를 `ValidationError`로 감싸버려
    `FA_ALLOCATION_RESIDUAL` 단일 예외 타입을 유지할 수 없기 때문이다."""

    sub_account_id: UUID
    weight: Decimal
    schema_version: Literal["v1"] = SCHEMA_VERSION


class ManualTarget(BaseModel):
    """`manual` 배분 대상 한 개 — 수량을 운영자가 직접 지정한다. 양수
    검증은 `allocate_manual`의 책임이다([[WeightTarget]]과 동일 이유)."""

    sub_account_id: UUID
    quantity: Decimal
    schema_version: Literal["v1"] = SCHEMA_VERSION


class AllocationLine(BaseModel):
    """배분 결과 한 줄."""

    sub_account_id: UUID
    quantity: Decimal
    schema_version: Literal["v1"] = SCHEMA_VERSION


def allocate(
    policy: AllocationPolicy,
    total_quantity: Decimal,
    *,
    weight_targets: Sequence[WeightTarget] = (),
    manual_targets: Sequence[ManualTarget] = (),
    quantum: Decimal = Decimal("1"),
) -> tuple[AllocationLine, ...]:
    """정책 이름으로 분기하는 단일 진입점. 미지 정책은 fail-closed로 거부한다."""
    if policy is AllocationPolicy.PRO_RATA:
        return allocate_pro_rata(total_quantity, weight_targets, quantum)
    if policy is AllocationPolicy.FIXED_WEIGHT:
        return allocate_fixed_weight(total_quantity, weight_targets, quantum)
    if policy is AllocationPolicy.MANUAL:
        return allocate_manual(total_quantity, manual_targets)
    raise AllocationResidualError(f"알 수 없는 배분 정책: {policy!r}")


def allocate_pro_rata(
    total_quantity: Decimal,
    targets: Sequence[WeightTarget],
    quantum: Decimal,
) -> tuple[AllocationLine, ...]:
    """비례 배분 — 가중치는 임의 양수(예: 기존 포지션 크기)이고 내부에서
    가중치 합으로 정규화한다."""
    return _allocate_by_weight(total_quantity, targets, quantum)


def allocate_fixed_weight(
    total_quantity: Decimal,
    targets: Sequence[WeightTarget],
    quantum: Decimal,
) -> tuple[AllocationLine, ...]:
    """고정비율 배분 — 가중치는 사전에 합의된 비율이라 합이 정확히 1이어야
    한다(정규화하지 않는다 — 합 불일치는 설정 오류이므로 조용히 넘어가지
    않고 거부한다)."""
    if not targets:
        raise AllocationResidualError("fixed_weight 배분 대상이 비어 있음")
    weight_sum = sum((t.weight for t in targets), Decimal("0"))
    if weight_sum != Decimal("1"):
        raise AllocationResidualError(f"fixed_weight 가중치 합은 1이어야 함: {weight_sum}")
    return _allocate_by_weight(total_quantity, targets, quantum)


def allocate_manual(
    total_quantity: Decimal,
    targets: Sequence[ManualTarget],
) -> tuple[AllocationLine, ...]:
    """수동 배분 — 라운딩이 개입할 자리가 없다. 지정된 수량 합이 체결
    수량과 정확히 같아야 한다."""
    if not targets:
        raise AllocationResidualError("manual 배분 대상이 비어 있음")
    for t in targets:
        if t.quantity <= 0:
            raise AllocationResidualError(
                f"sub_account {t.sub_account_id} quantity는 0보다 커야 함: {t.quantity}"
            )
    allocated_sum = sum((t.quantity for t in targets), Decimal("0"))
    if allocated_sum != total_quantity:
        raise AllocationResidualError(
            f"manual 배분 합({allocated_sum}) != 체결 수량({total_quantity})"
        )
    return tuple(
        AllocationLine(sub_account_id=t.sub_account_id, quantity=t.quantity) for t in targets
    )


def _allocate_by_weight(
    total_quantity: Decimal,
    targets: Sequence[WeightTarget],
    quantum: Decimal,
) -> tuple[AllocationLine, ...]:
    if total_quantity <= 0:
        raise AllocationResidualError(f"체결 수량은 0보다 커야 함: {total_quantity}")
    if not targets:
        raise AllocationResidualError("배분 대상이 비어 있음")
    if quantum <= 0:
        raise AllocationResidualError(f"quantum은 0보다 커야 함: {quantum}")
    for t in targets:
        if t.weight <= 0:
            raise AllocationResidualError(
                f"sub_account {t.sub_account_id} weight는 0보다 커야 함: {t.weight}"
            )

    weight_sum = sum((t.weight for t in targets), Decimal("0"))

    quantized = [
        (t, (total_quantity * t.weight / weight_sum).quantize(quantum, rounding=ROUND_HALF_EVEN))
        for t in targets
    ]
    allocated_sum = sum((q for _, q in quantized), Decimal("0"))
    residual = total_quantity - allocated_sum
    if residual % quantum != 0:
        raise AllocationResidualError(
            f"체결 수량({total_quantity})이 quantum({quantum})의 배수가 아니어서 "
            "잔여를 정확히 흡수할 수 없음"
        )
    residual_units = int(residual / quantum)

    # 잔여를 가중치 큰 순(동률은 sub_account_id 순 — 결정론적 재현)으로
    # quantum 단위씩 나눠 흡수시킨다. 가장 큰 배분에 작은 보정을 먼저
    # 얹는 편이 상대적 왜곡이 가장 작다.
    def _sort_key(i: int) -> tuple[Decimal, UUID]:
        return (-quantized[i][0].weight, quantized[i][0].sub_account_id)

    order = sorted(range(len(quantized)), key=_sort_key)
    quantities = [q for _, q in quantized]
    step = quantum if residual_units >= 0 else -quantum
    for i in range(abs(residual_units)):
        quantities[order[i % len(order)]] += step

    lines = tuple(
        AllocationLine(sub_account_id=quantized[i][0].sub_account_id, quantity=quantities[i])
        for i in range(len(quantized))
    )
    for line in lines:
        if line.quantity < 0:
            raise AllocationResidualError(
                f"sub_account {line.sub_account_id} 배분 수량이 음수({line.quantity}) — "
                "가중치·quantum 조합을 재검토할 것"
            )
    final_sum = sum((line.quantity for line in lines), Decimal("0"))
    if final_sum != total_quantity:
        raise AllocationResidualError(
            f"배분 합({final_sum}) != 체결 수량({total_quantity})"
        )
    return lines
