"""LB-6 — reconciliation_rules 단위테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-6
(`unit/positions/test_reconciliation_rules.py`:
"공급자 불일치 → MATERIAL 판정 경계값").
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.foundation.positions.domain.reconciliation_rules import (
    InternalEntityValue,
    break_age,
    build_entity_snapshots,
)
from src.foundation.reconciliation.contracts.v1 import EntitySnapshot
from src.foundation.reconciliation.domain.models import Classification, MaterialityPolicy
from src.foundation.reconciliation.domain.rules import classify_item

_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
_POLICY = MaterialityPolicy(
    absolute_tolerance=Decimal("0.01"), relative_tolerance_pct=Decimal("0.1")
)


def test_build_entity_snapshots_pairs_internal_with_provider_by_key() -> None:
    internal = [
        InternalEntityValue(entity_type="BALANCE", entity_key="USDT_BALANCE", value=Decimal("100")),
        InternalEntityValue(
            entity_type="POSITION", entity_key="BTCUSDT_POSITION", value=Decimal("1.5")
        ),
    ]
    provider = {"USDT_BALANCE": Decimal("100"), "BTCUSDT_POSITION": Decimal("1.4")}

    result = build_entity_snapshots(internal, provider)

    assert result == [
        EntitySnapshot(
            entity_type="BALANCE",
            entity_key="USDT_BALANCE",
            internal_value=Decimal("100"),
            provider_value=Decimal("100"),
        ),
        EntitySnapshot(
            entity_type="POSITION",
            entity_key="BTCUSDT_POSITION",
            internal_value=Decimal("1.5"),
            provider_value=Decimal("1.4"),
        ),
    ]


def test_build_entity_snapshots_missing_provider_key_is_none_not_zero() -> None:
    """공급자 응답에 entity_key가 아예 없으면 0이 아니라 None이어야 한다
    (FND-08 §2 "0으로 해석하지 않는다")."""
    internal = [
        InternalEntityValue(entity_type="BALANCE", entity_key="KRW_BALANCE", value=Decimal("500"))
    ]
    provider: dict[str, Decimal | None] = {}

    result = build_entity_snapshots(internal, provider)

    assert result[0].provider_value is None


def test_build_entity_snapshots_provider_explicit_none_is_preserved() -> None:
    internal = [
        InternalEntityValue(entity_type="BALANCE", entity_key="KRW_BALANCE", value=Decimal("500"))
    ]
    provider: dict[str, Decimal | None] = {"KRW_BALANCE": None}

    result = build_entity_snapshots(internal, provider)

    assert result[0].provider_value is None


def test_build_entity_snapshots_empty_internal_is_empty_list() -> None:
    assert build_entity_snapshots([], {"USDT_BALANCE": Decimal("1")}) == []


def test_build_entity_snapshots_rejects_duplicate_entity_key() -> None:
    internal = [
        InternalEntityValue(entity_type="BALANCE", entity_key="USDT_BALANCE", value=Decimal("100")),
        InternalEntityValue(entity_type="BALANCE", entity_key="USDT_BALANCE", value=Decimal("200")),
    ]

    with pytest.raises(ValueError, match="USDT_BALANCE"):
        build_entity_snapshots(internal, {})


def test_break_age_computes_elapsed_timedelta() -> None:
    detected_at = _NOW - timedelta(minutes=7)

    assert break_age(detected_at, _NOW) == timedelta(minutes=7)


def test_break_age_rejects_naive_detected_at() -> None:
    with pytest.raises(ValueError, match="detected_at"):
        break_age(datetime(2026, 9, 3, 11, 0), _NOW)


def test_break_age_rejects_naive_now() -> None:
    with pytest.raises(ValueError, match="now"):
        break_age(_NOW - timedelta(minutes=1), datetime(2026, 9, 3, 12, 0))


def test_material_mismatch_boundary_just_within_absolute_tolerance_is_minor() -> None:
    """diff == absolute_tolerance(0.01) 경계값 — 아직 MINOR."""
    internal = [
        InternalEntityValue(
            entity_type="BALANCE", entity_key="USDT_BALANCE", value=Decimal("100.00")
        )
    ]
    provider = {"USDT_BALANCE": Decimal("99.99")}  # diff = 0.01

    snapshot = build_entity_snapshots(internal, provider)[0]

    result = classify_item(snapshot.internal_value, snapshot.provider_value, _POLICY)

    assert result == Classification.MINOR_DIFFERENCE


def test_material_mismatch_boundary_just_beyond_absolute_tolerance_is_material() -> None:
    """diff가 absolute_tolerance를 0.01 넘어서고 relative_tolerance_pct(0.1%)도
    넘는 경계값 — MATERIAL."""
    internal = [
        InternalEntityValue(
            entity_type="BALANCE", entity_key="USDT_BALANCE", value=Decimal("100.00")
        )
    ]
    provider = {"USDT_BALANCE": Decimal("99.88")}  # diff = 0.12 (> 0.01, > 0.1%)

    snapshot = build_entity_snapshots(internal, provider)[0]

    result = classify_item(snapshot.internal_value, snapshot.provider_value, _POLICY)

    assert result == Classification.MATERIAL_MISMATCH


def test_provider_missing_entity_classifies_as_provider_unavailable_not_material() -> None:
    internal = [
        InternalEntityValue(
            entity_type="POSITION", entity_key="BTCUSDT_POSITION", value=Decimal("1")
        )
    ]

    snapshot = build_entity_snapshots(internal, {})[0]

    result = classify_item(snapshot.internal_value, snapshot.provider_value, _POLICY)

    assert result == Classification.PROVIDER_UNAVAILABLE
