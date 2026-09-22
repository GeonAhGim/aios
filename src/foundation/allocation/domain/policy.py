"""FA-7 — allocation/domain/policy.py: three-policy allocation (pure domain).

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-7 (§2.2 table, §4 FA-A3).

All three policies — `pro_rata` / `fixed_weight` / `manual` — must satisfy
a single invariant (FA-A3): "when splitting a fill quantity across multiple
sub_accounts, the sum must always equal the original quantity exactly."
`pro_rata` and `fixed_weight` share the same calculation — the only
difference is the source of the weight vector (pro_rata: arbitrary positive
ratios, normalized at runtime; fixed_weight: pre-agreed ratios that must sum
to exactly 1, not normalized). Both share `_allocate_by_weight`.

Rounding follows the principle of [[rounding]]
(`src/foundation/ledger/domain/rounding.py`, LC-2): "preserve the sum by
concentrating rounding remainder into buckets according to a fixed rule."
The function itself (`split_commission`) is dedicated to 2-way, fixed KRW
quantum and cannot be reused for this leaf's N-way, arbitrary-quantum case
(not a reimplementation, but an N-way generalization of the same principle).
Each target is rounded to quantum granularity using `ROUND_HALF_EVEN`, then
the rounding error (= fill quantity − sum of rounded quantities) is corrected
in quantum-sized steps from the largest-weight targets downward — so the
target with the least relative distortion absorbs the remainder first.
Pure domain (zero I/O / DB imports).
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum
from typing import ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel

SCHEMA_VERSION: Literal["v1"] = "v1"


def round_to_quantum(value: Decimal, quantum: Decimal) -> Decimal:
    """Round `value` to the nearest multiple of `quantum` (`ROUND_HALF_EVEN`).

    [[QA finding]] `Decimal.quantize(quantum)` does not round to a multiple
    of `quantum` — it aligns the exponent (decimal places) to match
    `quantum`. Integer literals all have exponent 0, so `quantum=1`, `10`,
    and `100` all produce identical "round to integer" behavior (values that
    happen to be correct are limited to pure decimals like `1`, `0.1`,
    `0.01`). You must round the quotient to an integer and then multiply by
    quantum to get an actual multiple.
    """
    units = (value / quantum).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    return units * quantum


class AllocationErrorCode(str, Enum):
    """Allocation-related error codes defined by this leaf (FA-7) among the
    §3 error taxonomy."""

    RESIDUAL = "FA_ALLOCATION_RESIDUAL"  # 409, allocation residual / input mismatch


class AllocationResidualError(ValueError):
    """FA_ALLOCATION_RESIDUAL(409) — allocation sum diverges from fill
    quantity, or input is in an unallocatable state (weight sum mismatch,
    zero/negative quantity, unknown policy, etc.)."""

    code: ClassVar[AllocationErrorCode] = AllocationErrorCode.RESIDUAL


class AllocationPolicy(str, Enum):
    PRO_RATA = "pro_rata"
    FIXED_WEIGHT = "fixed_weight"
    MANUAL = "manual"


class WeightTarget(BaseModel):
    """A single `pro_rata`/`fixed_weight` allocation target. Positive-value
    validation is not this model's responsibility — it belongs to the
    allocation function (`_allocate_by_weight`) (FA-1 `contracts/v1.py`
    convention — contracts define shape only, invariants live in domain
    functions) — because pydantic `model_validator` wraps `ValueError` into
    `ValidationError`, making it impossible to maintain the single
    `FA_ALLOCATION_RESIDUAL` exception type."""

    sub_account_id: UUID
    weight: Decimal
    schema_version: Literal["v1"] = SCHEMA_VERSION


class ManualTarget(BaseModel):
    """A single `manual` allocation target — the operator specifies quantities
    directly. Positive-value validation is `allocate_manual`'s responsibility
    (same reason as [[WeightTarget]])."""

    sub_account_id: UUID
    quantity: Decimal
    schema_version: Literal["v1"] = SCHEMA_VERSION


class AllocationLine(BaseModel):
    """A single allocation result line."""

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
    """Single entry point branching on policy name. Unknown policies are
    rejected fail-closed."""
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
    """Pro-rata allocation — weights are arbitrary positive numbers (e.g.
    existing position sizes) and are normalized by their sum internally."""
    return _allocate_by_weight(total_quantity, targets, quantum)


def allocate_fixed_weight(
    total_quantity: Decimal,
    targets: Sequence[WeightTarget],
    quantum: Decimal,
) -> tuple[AllocationLine, ...]:
    """Fixed-weight allocation — weights are pre-agreed ratios that must sum
    to exactly 1 (not normalized — a sum mismatch is a configuration error
    and must be rejected, not silently ignored)."""
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
    """Manual allocation — no rounding applies. The sum of specified
    quantities must equal the fill quantity exactly."""
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
        (t, round_to_quantum(total_quantity * t.weight / weight_sum, quantum))
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

    # Distribute the remainder in quantum-sized steps from largest weight
    # (ties broken by sub_account_id — deterministic reproducibility).
    # Applying small corrections to the largest allocations first yields
    # the least relative distortion.
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
