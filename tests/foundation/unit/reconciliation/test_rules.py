"""FND-08 Reconciliation & Resilience 순수 규칙 단위테스트 — DB 없음."""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation, localcontext
from time import perf_counter
from typing import cast
from unittest.mock import Mock

import pytest

from src.foundation.reconciliation.domain import rules as rules_module
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


def test_classify_item_negative_values_beyond_tolerance_is_material():
    """음수 internal/provider 값 — 경계값·잘못된 부호 입력."""
    assert (
        classify_item(Decimal("-100.00"), Decimal("-50.00"), _POLICY)
        == Classification.MATERIAL_MISMATCH
    )


def test_classify_item_exactly_at_absolute_tolerance_boundary_is_minor():
    """diff == absolute_tolerance 경계값 — <= 이므로 MINOR로 분류돼야 한다."""
    assert (
        classify_item(Decimal("100.00"), Decimal("100.01"), _POLICY)
        == Classification.MINOR_DIFFERENCE
    )


def test_classify_item_both_zero_is_healthy():
    """internal/provider 둘 다 0 — relative_base도 0이 되는 경계값."""
    assert classify_item(Decimal("0"), Decimal("0"), _POLICY) == Classification.HEALTHY


def test_aggregate_classification_unclassified_states_fall_back_to_healthy():
    """PENDING/INVESTIGATING/RESOLVED처럼 _SEVERITY_ORDER에 없는 상태만 있으면
    집계 규칙이 기본값 HEALTHY로 떨어진다(§2 순서 매칭 실패 시 폴백 분기 검증)."""
    items = (Classification.RESOLVED, Classification.INVESTIGATING)
    assert aggregate_classification(items) == Classification.HEALTHY


def test_compute_input_hash_raises_on_non_serializable_entities():
    """잘못된 입력(직렬화 불가능한 값) — json.dumps가 TypeError를 전파해야 하며
    조용히 성공한 것처럼 위장하지 않는다."""
    bad_entities = cast("dict[str, tuple[str, str]]", {"BAD": (object(), "1")})
    with pytest.raises(TypeError):
        compute_input_hash("target-1", bad_entities)


def test_compute_input_hash_propagates_hashing_failure(monkeypatch):
    """실패주입 — sha256 계산이 예외를 던지면 compute_input_hash는 이를 숨기지 않고
    그대로 전파해야 한다(fail-closed)."""

    def _boom(_data: bytes) -> None:
        raise RuntimeError("hash backend unavailable")

    monkeypatch.setattr(rules_module.hashlib, "sha256", _boom)
    with pytest.raises(RuntimeError, match="hash backend unavailable"):
        compute_input_hash("target-1", {"USDT_BALANCE": ("100.00", "100.00")})


def test_compute_input_hash_payload_matches_expected_json_shape():
    """해시 입력 payload가 target_ref/entities를 정렬된 키로 직렬화한다는
    내부 계약 확인 — dedup 안정성의 근거."""
    entities = {"B": ("1", "2"), "A": ("3", "4")}
    expected_payload = json.dumps(
        {"target_ref": "t", "entities": entities}, sort_keys=True
    ).encode("utf-8")
    import hashlib as real_hashlib

    assert compute_input_hash("t", entities) == real_hashlib.sha256(expected_payload).hexdigest()


@pytest.mark.parametrize("invalid", [Decimal("NaN"), Decimal("sNaN")])
@pytest.mark.parametrize("side", ["internal", "provider"])
def test_negative_classify_item_rejects_nan(invalid, side):
    """Non-numeric balances must not yield a successful reconciliation grade."""
    internal = invalid if side == "internal" else Decimal("100")
    provider = invalid if side == "provider" else Decimal("100")
    with localcontext() as context:
        context.traps[InvalidOperation] = True
        with pytest.raises(InvalidOperation):
            classify_item(internal, provider, _POLICY)


@pytest.mark.parametrize("side", ["internal", "provider"])
def test_negative_classify_item_rejects_float_amount(side):
    """Money uses Decimal; mixing binary floats must fail closed."""
    invalid = cast(Decimal, 100.0)
    internal = invalid if side == "internal" else Decimal("100")
    provider = invalid if side == "provider" else Decimal("100")
    with pytest.raises(TypeError):
        classify_item(internal, provider, _POLICY)


def test_negative_compute_input_hash_rejects_unconverted_decimal():
    """REC-004/006 requires explicit string amounts in the dedup payload."""
    entities = cast("dict[str, tuple[str, str]]", {"USDT": (Decimal("1.01"), "1.01")})
    with pytest.raises(TypeError, match="Decimal.*not JSON serializable"):
        compute_input_hash("target-1", entities)


def test_failure_injection_serialization_error_prevents_hashing(monkeypatch):
    """A serialization failure must never produce a dedup hash for partial input."""
    failure = RuntimeError("injected serialization failure")
    serialize = Mock(side_effect=failure)
    hash_backend = Mock()
    monkeypatch.setattr(rules_module.json, "dumps", serialize)
    monkeypatch.setattr(rules_module.hashlib, "sha256", hash_backend)
    with pytest.raises(RuntimeError, match="injected serialization failure") as caught:
        compute_input_hash("target-1", {"USDT": ("100", "99")})
    assert caught.value is failure
    serialize.assert_called_once()
    hash_backend.assert_not_called()


def test_adversarial_missing_zero_balance_cannot_aggregate_as_healthy():
    """REC-003 / I-07: missing external evidence must not become a zero match."""
    unavailable = classify_item(Decimal("0"), None, _POLICY)
    assert unavailable == Classification.PROVIDER_UNAVAILABLE
    assert aggregate_classification((Classification.HEALTHY,) * 100 + (unavailable,)) == (
        Classification.PROVIDER_UNAVAILABLE
    )


def test_adversarial_material_mismatch_cannot_be_diluted_by_healthy_items():
    """I-07: a single material discrepancy survives healthy and unavailable items."""
    mismatch = classify_item(Decimal("100"), Decimal("99"), _POLICY)
    assert mismatch == Classification.MATERIAL_MISMATCH
    items = (Classification.HEALTHY,) * 100 + (Classification.PROVIDER_UNAVAILABLE, mismatch)
    assert aggregate_classification(items) == Classification.MATERIAL_MISMATCH


def test_classification_performance_p99_within_gate_budget():
    """ADR-2026-09-09-C: this pure-rule portion stays within the 5ms gate budget."""
    internal, provider = Decimal("100"), Decimal("99")
    samples = []
    for _ in range(200):
        started = perf_counter()
        result = aggregate_classification((classify_item(internal, provider, _POLICY),))
        samples.append(perf_counter() - started)
        assert result == Classification.MATERIAL_MISMATCH
    assert sorted(samples)[197] < 0.005
