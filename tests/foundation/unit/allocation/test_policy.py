"""FA-7 domain/policy.py — 3정책 배분 단위테스트(순수 함수만, DB 없음)."""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.allocation.domain.policy import (
    AllocationPolicy,
    AllocationResidualError,
    ManualTarget,
    WeightTarget,
    allocate,
    allocate_fixed_weight,
    allocate_manual,
    allocate_pro_rata,
)


def _sum(lines) -> Decimal:
    return sum((line.quantity for line in lines), Decimal("0"))


def _equal_weights(*ids):
    return [WeightTarget(sub_account_id=i, weight=Decimal("1")) for i in ids]


# ---- pro_rata ----


def test_pro_rata_exact_split_no_rounding():
    a, b = uuid4(), uuid4()
    targets = _equal_weights(a, b)
    lines = allocate_pro_rata(Decimal("100"), targets, Decimal("1"))
    by_id = {line.sub_account_id: line.quantity for line in lines}
    assert by_id[a] == Decimal("50")
    assert by_id[b] == Decimal("50")
    assert _sum(lines) == Decimal("100")


def test_pro_rata_normalizes_arbitrary_weights():
    a, b, c = uuid4(), uuid4(), uuid4()
    targets = [
        WeightTarget(sub_account_id=a, weight=Decimal("2")),
        WeightTarget(sub_account_id=b, weight=Decimal("3")),
        WeightTarget(sub_account_id=c, weight=Decimal("5")),
    ]
    lines = allocate_pro_rata(Decimal("100"), targets, Decimal("1"))
    by_id = {line.sub_account_id: line.quantity for line in lines}
    assert by_id[a] == Decimal("20")
    assert by_id[b] == Decimal("30")
    assert by_id[c] == Decimal("50")
    assert _sum(lines) == Decimal("100")


def test_pro_rata_residual_absorbed_to_largest_weight_first():
    # 100을 3등분 -> 33.33.. 각 33으로 내림/반올림되면 잔여 1이 생긴다.
    a, b, c = uuid4(), uuid4(), uuid4()
    targets = _equal_weights(a, b, c)
    lines = allocate_pro_rata(Decimal("100"), targets, Decimal("1"))
    assert _sum(lines) == Decimal("100")
    quantities = sorted((line.quantity for line in lines), reverse=True)
    assert quantities[0] == Decimal("34")
    assert quantities[1:] == [Decimal("33"), Decimal("33")]


def test_pro_rata_rejects_zero_total_quantity():
    a = uuid4()
    with pytest.raises(AllocationResidualError):
        allocate_pro_rata(Decimal("0"), _equal_weights(a), Decimal("1"))


def test_pro_rata_rejects_negative_total_quantity():
    a = uuid4()
    with pytest.raises(AllocationResidualError):
        allocate_pro_rata(Decimal("-10"), _equal_weights(a), Decimal("1"))


def test_pro_rata_rejects_zero_weight_target():
    a, b = uuid4(), uuid4()
    targets = [
        WeightTarget(sub_account_id=a, weight=Decimal("1")),
        WeightTarget(sub_account_id=b, weight=Decimal("0")),
    ]
    with pytest.raises(AllocationResidualError):
        allocate_pro_rata(Decimal("10"), targets, Decimal("1"))


def test_pro_rata_rejects_negative_weight_target():
    a, b = uuid4(), uuid4()
    targets = [
        WeightTarget(sub_account_id=a, weight=Decimal("1")),
        WeightTarget(sub_account_id=b, weight=Decimal("-1")),
    ]
    with pytest.raises(AllocationResidualError):
        allocate_pro_rata(Decimal("10"), targets, Decimal("1"))


def test_pro_rata_rejects_total_finer_than_quantum():
    # 체결 수량(10.005)이 quantum(0.01)보다 더 세밀한 정밀도를 가지면
    # 반올림 잔여(0.005)가 quantum의 배수가 될 수 없다.
    a, b = uuid4(), uuid4()
    targets = _equal_weights(a, b)
    with pytest.raises(AllocationResidualError):
        allocate_pro_rata(Decimal("10.005"), targets, Decimal("0.01"))


# ---- fixed_weight ----


def test_fixed_weight_exact_split():
    a, b = uuid4(), uuid4()
    targets = [
        WeightTarget(sub_account_id=a, weight=Decimal("0.6")),
        WeightTarget(sub_account_id=b, weight=Decimal("0.4")),
    ]
    lines = allocate_fixed_weight(Decimal("100"), targets, Decimal("1"))
    by_id = {line.sub_account_id: line.quantity for line in lines}
    assert by_id[a] == Decimal("60")
    assert by_id[b] == Decimal("40")
    assert _sum(lines) == Decimal("100")


def test_fixed_weight_rejects_weight_sum_not_one():
    a, b = uuid4(), uuid4()
    targets = [
        WeightTarget(sub_account_id=a, weight=Decimal("0.6")),
        WeightTarget(sub_account_id=b, weight=Decimal("0.5")),
    ]
    with pytest.raises(AllocationResidualError):
        allocate_fixed_weight(Decimal("100"), targets, Decimal("1"))


def test_fixed_weight_rejects_empty_targets():
    with pytest.raises(AllocationResidualError):
        allocate_fixed_weight(Decimal("100"), [], Decimal("1"))


# ---- manual ----


def test_manual_exact_assignment():
    a, b = uuid4(), uuid4()
    targets = [
        ManualTarget(sub_account_id=a, quantity=Decimal("70")),
        ManualTarget(sub_account_id=b, quantity=Decimal("30")),
    ]
    lines = allocate_manual(Decimal("100"), targets)
    assert _sum(lines) == Decimal("100")


def test_manual_rejects_sum_mismatch():
    a, b = uuid4(), uuid4()
    targets = [
        ManualTarget(sub_account_id=a, quantity=Decimal("70")),
        ManualTarget(sub_account_id=b, quantity=Decimal("20")),
    ]
    with pytest.raises(AllocationResidualError):
        allocate_manual(Decimal("100"), targets)


def test_manual_rejects_zero_quantity_target():
    a, b = uuid4(), uuid4()
    targets = [
        ManualTarget(sub_account_id=a, quantity=Decimal("100")),
        ManualTarget(sub_account_id=b, quantity=Decimal("0")),
    ]
    with pytest.raises(AllocationResidualError):
        allocate_manual(Decimal("100"), targets)


def test_manual_rejects_negative_quantity_target():
    a, b = uuid4(), uuid4()
    targets = [
        ManualTarget(sub_account_id=a, quantity=Decimal("110")),
        ManualTarget(sub_account_id=b, quantity=Decimal("-10")),
    ]
    with pytest.raises(AllocationResidualError):
        allocate_manual(Decimal("100"), targets)


def test_manual_rejects_empty_targets():
    with pytest.raises(AllocationResidualError):
        allocate_manual(Decimal("100"), [])


# ---- allocate() 디스패처 ----


def test_allocate_dispatches_pro_rata():
    a, b = uuid4(), uuid4()
    targets = _equal_weights(a, b)
    lines = allocate(
        AllocationPolicy.PRO_RATA, Decimal("10"), weight_targets=targets, quantum=Decimal("1")
    )
    assert _sum(lines) == Decimal("10")


def test_allocate_rejects_unknown_policy():
    with pytest.raises(AllocationResidualError):
        allocate("unknown_policy", Decimal("10"))  # type: ignore[arg-type]
