"""FND-08 Reconciliation & Resilience 순수 규칙 단위테스트 — DB 없음."""
from __future__ import annotations

from decimal import Decimal

from src.foundation.reconciliation.domain.models import Classification, MaterialityPolicy
from src.foundation.reconciliation.domain.rules import (
    aggregate_classification,
    classify_item,
    compute_input_hash,
)

_POLICY = MaterialityPolicy(
    absolute_tolerance=Decimal("0.01"), relative_tolerance_pct=Decimal("0.1")
)


def test_classify_item_exact_match_is_healthy():
    assert classify_item(Decimal("100.00"), Decimal("100.00"), _POLICY) == Classification.HEALTHY


def test_classify_item_missing_provider_value_is_unavailable():
    """REC-003 — never assumes zero balance/fill."""
    assert (
        classify_item(Decimal("100.00"), None, _POLICY) == Classification.PROVIDER_UNAVAILABLE
    )


def test_classify_item_within_absolute_tolerance_is_minor():
    assert (
        classify_item(Decimal("100.00"), Decimal("100.005"), _POLICY)
        == Classification.MINOR_DIFFERENCE
    )


def test_classify_item_beyond_tolerance_is_material():
    assert (
        classify_item(Decimal("100.00"), Decimal("50.00"), _POLICY)
        == Classification.MATERIAL_MISMATCH
    )


def test_classify_item_zero_internal_uses_provider_as_relative_base():
    """internal_value가 0이면 상대오차 기준을 provider_value로 잡는다 —
    0으로 나누기를 피하면서도 무조건 MATERIAL로 떨어지지 않게 한다."""
    result = classify_item(Decimal("0"), Decimal("0.001"), _POLICY)
    assert result in (Classification.HEALTHY, Classification.MINOR_DIFFERENCE)


def test_aggregate_classification_empty_is_pending():
    assert aggregate_classification(()) == Classification.PENDING


def test_aggregate_classification_worst_of_wins():
    """80번 §2 심각도 순서 — MATERIAL_MISMATCH가 다른 무엇보다 우선한다."""
    items = (
        Classification.HEALTHY,
        Classification.MINOR_DIFFERENCE,
        Classification.MATERIAL_MISMATCH,
        Classification.PROVIDER_UNAVAILABLE,
    )
    assert aggregate_classification(items) == Classification.MATERIAL_MISMATCH


def test_aggregate_classification_all_healthy_is_healthy():
    assert aggregate_classification((Classification.HEALTHY, Classification.HEALTHY)) == (
        Classification.HEALTHY
    )


def test_compute_input_hash_stable_for_same_input():
    """REC-004/006 — 같은 입력이면 항상 같은 해시(dedup 근거)."""
    entities = {"USDT_BALANCE": ("100.00", "100.00")}
    a = compute_input_hash("target-1", entities)
    b = compute_input_hash("target-1", entities)
    assert a == b


def test_compute_input_hash_differs_for_different_values():
    a = compute_input_hash("target-1", {"USDT_BALANCE": ("100.00", "100.00")})
    b = compute_input_hash("target-1", {"USDT_BALANCE": ("100.00", "50.00")})
    assert a != b
