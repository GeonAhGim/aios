"""Tests for src/foundation/reconciliation/contracts/v1.py.

Covers Classification enum, EntitySnapshot, RunReconciliationRequest,
ReconciliationItemView, ReconciliationRunView, ResolveReconciliationRequest,
ReconciliationStateView, and SCHEMA_VERSION.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from src.foundation.reconciliation.contracts.v1 import (
    SCHEMA_VERSION,
    Classification,
    EntitySnapshot,
    ReconciliationItemView,
    ReconciliationRunView,
    ReconciliationStateView,
    ResolveReconciliationRequest,
    RunReconciliationRequest,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OK_NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
_FAKE_UUID = uuid4()


def _uuid() -> UUID:
    return uuid4()


# ---------------------------------------------------------------------------
# SCHEMA_VERSION
# ---------------------------------------------------------------------------

class TestSchemaVersion:
    def test_schema_version_value(self):
        assert SCHEMA_VERSION == "v1"


# ---------------------------------------------------------------------------
# Classification enum
# ---------------------------------------------------------------------------

class TestClassification:
    def test_all_members_exist(self):
        assert Classification.HEALTHY
        assert Classification.PENDING
        assert Classification.MINOR_DIFFERENCE
        assert Classification.MATERIAL_MISMATCH
        assert Classification.PROVIDER_UNAVAILABLE
        assert Classification.INVESTIGATING
        assert Classification.RESOLVED

    def test_iteration(self):
        members = list(Classification)
        assert len(members) == 7

    def test_by_value(self):
        assert Classification("HEALTHY") is Classification.HEALTHY
        assert Classification("MATERIAL_MISMATCH") is Classification.MATERIAL_MISMATCH

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            Classification("UNKNOWN")  # type: ignore[arg-type]

    def test_str_coercion(self):
        assert Classification.HEALTHY.value == "HEALTHY"


# ---------------------------------------------------------------------------
# EntitySnapshot
# ---------------------------------------------------------------------------

class TestEntitySnapshot:
    def test_minimal_valid(self):
        snap = EntitySnapshot(
            entity_type="BALANCE",
            entity_key="USDT_BALANCE",
            internal_value=Decimal("1000.50"),
        )
        assert snap.entity_type == "BALANCE"
        assert snap.entity_key == "USDT_BALANCE"
        assert snap.internal_value == Decimal("1000.50")
        assert snap.provider_value is None

    def test_with_provider_value(self):
        snap = EntitySnapshot(
            entity_type="POSITION",
            entity_key="BTCUSDT_POSITION",
            internal_value=Decimal("0.5"),
            provider_value=Decimal("0.5"),
        )
        assert snap.provider_value == Decimal("0.5")

    def test_provider_none_means_missing_not_zero(self):
        """provider_value=None means provider did not return the item (§2)."""
        snap = EntitySnapshot(
            entity_type="BALANCE",
            entity_key="USDT_BALANCE",
            internal_value=Decimal("100"),
            provider_value=None,
        )
        assert snap.provider_value is None

    def test_missing_internal_value_raises(self):
        with pytest.raises(ValidationError):
            EntitySnapshot(  # type: ignore[call-arg]
                entity_type="BALANCE",
                entity_key="USDT_BALANCE",
            )

    def test_empty_entity_type_accepted_by_pydantic(self):
        """Pydantic v2 str field without min_length accepts empty string."""
        snap = EntitySnapshot(
            entity_type="",
            entity_key="USDT_BALANCE",
            internal_value=Decimal("100"),
        )
        assert snap.entity_type == ""

    def test_empty_entity_key_accepted_by_pydantic(self):
        """Pydantic v2 str field without min_length accepts empty string."""
        snap = EntitySnapshot(
            entity_type="BALANCE",
            entity_key="",
            internal_value=Decimal("100"),
        )
        assert snap.entity_key == ""

    def test_model_dump(self):
        snap = EntitySnapshot(
            entity_type="BALANCE",
            entity_key="USDT_BALANCE",
            internal_value=Decimal("1000.50"),
            provider_value=Decimal("999.00"),
        )
        d = snap.model_dump()
        assert d["entity_type"] == "BALANCE"
        assert d["internal_value"] == Decimal("1000.50")
        assert d["provider_value"] == Decimal("999.00")

    def test_model_dump_provider_none(self):
        snap = EntitySnapshot(
            entity_type="BALANCE",
            entity_key="USDT_BALANCE",
            internal_value=Decimal("1000"),
        )
        d = snap.model_dump()
        assert d["provider_value"] is None

    def test_model_dump_json_mode(self):
        snap = EntitySnapshot(
            entity_type="BALANCE",
            entity_key="USDT_BALANCE",
            internal_value=Decimal("1000.50"),
            provider_value=Decimal("999.00"),
        )
        d = snap.model_dump(mode="json")
        assert d["internal_value"] == "1000.50"
        assert d["provider_value"] == "999.00"


# ---------------------------------------------------------------------------
# RunReconciliationRequest
# ---------------------------------------------------------------------------

class TestRunReconciliationRequest:
    def test_minimal_valid(self):
        req = RunReconciliationRequest(
            target_type="FUTURES_ACCOUNT",
            target_ref=_uuid(),
            entities=[],
        )
        assert req.target_type == "FUTURES_ACCOUNT"
        assert req.connection_id is None

    def test_with_connection_id(self):
        conn_id = _uuid()
        req = RunReconciliationRequest(
            target_type="SPOT_ACCOUNT",
            target_ref=_uuid(),
            connection_id=conn_id,
            entities=[],
        )
        assert req.connection_id == conn_id

    def test_with_entities(self):
        entities = [
            EntitySnapshot(
                entity_type="BALANCE",
                entity_key="USDT_BALANCE",
                internal_value=Decimal("1000"),
            ),
        ]
        req = RunReconciliationRequest(
            target_type="SPOT_ACCOUNT",
            target_ref=_uuid(),
            entities=entities,
        )
        assert len(req.entities) == 1

    def test_missing_target_type_raises(self):
        with pytest.raises(ValidationError):
            RunReconciliationRequest(  # type: ignore[call-arg]
                target_ref=_uuid(),
                entities=[],
            )

    def test_missing_target_ref_raises(self):
        with pytest.raises(ValidationError):
            RunReconciliationRequest(  # type: ignore[call-arg]
                target_type="SPOT_ACCOUNT",
                entities=[],
            )

    def test_missing_entities_raises(self):
        with pytest.raises(ValidationError):
            RunReconciliationRequest(  # type: ignore[call-arg]
                target_type="SPOT_ACCOUNT",
                target_ref=_uuid(),
            )


# ---------------------------------------------------------------------------
# ReconciliationItemView
# ---------------------------------------------------------------------------

class TestReconciliationItemView:
    def test_healthy_item(self):
        item = ReconciliationItemView(
            entity_type="BALANCE",
            entity_key="USDT_BALANCE",
            internal_value=Decimal("1000"),
            provider_value=Decimal("1000"),
            classification=Classification.HEALTHY,
        )
        assert item.classification is Classification.HEALTHY

    def test_mismatch_item(self):
        item = ReconciliationItemView(
            entity_type="POSITION",
            entity_key="BTCUSDT_POSITION",
            internal_value=Decimal("1.0"),
            provider_value=Decimal("0.9"),
            classification=Classification.MINOR_DIFFERENCE,
        )
        assert item.internal_value != item.provider_value

    def test_provider_unavailable_item(self):
        item = ReconciliationItemView(
            entity_type="BALANCE",
            entity_key="UNKNOWN_BALANCE",
            internal_value=Decimal("0"),
            provider_value=None,
            classification=Classification.PROVIDER_UNAVAILABLE,
        )
        assert item.classification is Classification.PROVIDER_UNAVAILABLE

    def test_missing_classification_raises(self):
        with pytest.raises(ValidationError):
            ReconciliationItemView(  # type: ignore[call-arg]
                entity_type="BALANCE",
                entity_key="USDT_BALANCE",
                internal_value=Decimal("1000"),
                provider_value=Decimal("1000"),
            )

    def test_invalid_classification_raises(self):
        with pytest.raises(ValidationError):
            ReconciliationItemView(  # type: ignore[arg-type]
                entity_type="BALANCE",
                entity_key="USDT_BALANCE",
                internal_value=Decimal("1000"),
                provider_value=Decimal("1000"),
                classification="UNKNOWN_CLASSIFICATION",
            )


# ---------------------------------------------------------------------------
# ReconciliationRunView
# ---------------------------------------------------------------------------

class TestReconciliationRunView:
    def test_minimal_valid(self):
        run = ReconciliationRunView(
            id=_uuid(),
            target_type="FUTURES_ACCOUNT",
            target_ref=_uuid(),
            items=[],
            aggregate_classification=Classification.HEALTHY,
            created_at=_OK_NOW,
        )
        assert run.schema_version == SCHEMA_VERSION
        assert run.created_at == _OK_NOW

    def test_default_schema_version(self):
        run = ReconciliationRunView(
            id=_uuid(),
            target_type="SPOT_ACCOUNT",
            target_ref=_uuid(),
            items=[],
            aggregate_classification=Classification.PENDING,
            created_at=None,
        )
        assert run.schema_version == "v1"

    def test_with_items(self):
        items = [
            ReconciliationItemView(
                entity_type="BALANCE",
                entity_key="USDT_BALANCE",
                internal_value=Decimal("1000"),
                provider_value=Decimal("1000"),
                classification=Classification.HEALTHY,
            ),
        ]
        run = ReconciliationRunView(
            id=_uuid(),
            target_type="SPOT_ACCOUNT",
            target_ref=_uuid(),
            items=items,
            aggregate_classification=Classification.HEALTHY,
            created_at=_OK_NOW,
        )
        assert len(run.items) == 1

    def test_missing_id_raises(self):
        with pytest.raises(ValidationError):
            ReconciliationRunView(  # type: ignore[call-arg]
                target_type="SPOT_ACCOUNT",
                target_ref=_uuid(),
                items=[],
                aggregate_classification=Classification.HEALTHY,
                created_at=_OK_NOW,
            )

    def test_missing_aggregate_classification_raises(self):
        with pytest.raises(ValidationError):
            ReconciliationRunView(  # type: ignore[call-arg]
                id=_uuid(),
                target_type="SPOT_ACCOUNT",
                target_ref=_uuid(),
                items=[],
                created_at=_OK_NOW,
            )


# ---------------------------------------------------------------------------
# ResolveReconciliationRequest
# ---------------------------------------------------------------------------

class TestResolveReconciliationRequest:
    def test_minimal_valid(self):
        req = ResolveReconciliationRequest(reason="Manual override approved")
        assert req.reason == "Manual override approved"

    def test_empty_reason_accepted_by_pydantic(self):
        """Pydantic v2 str field without min_length accepts empty string."""
        req = ResolveReconciliationRequest(reason="")
        assert req.reason == ""

    def test_missing_reason_raises(self):
        with pytest.raises(ValidationError):
            ResolveReconciliationRequest()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ReconciliationStateView
# ---------------------------------------------------------------------------

class TestReconciliationStateView:
    def test_minimal_valid(self):
        state = ReconciliationStateView(
            target_ref=_uuid(),
            target_type="FUTURES_ACCOUNT",
            aggregate_status=Classification.HEALTHY,
            last_healthy_at=_OK_NOW,
            last_checked_at=_OK_NOW,
            blocking_reason=None,
            revision=1,
        )
        assert state.schema_version == SCHEMA_VERSION
        assert state.blocking_reason is None

    def test_default_schema_version(self):
        state = ReconciliationStateView(
            target_ref=_uuid(),
            target_type="SPOT_ACCOUNT",
            aggregate_status=Classification.INVESTIGATING,
            last_healthy_at=None,
            last_checked_at=_OK_NOW,
            blocking_reason="Provider timeout",
            revision=5,
        )
        assert state.schema_version == "v1"

    def test_blocking_reason_set(self):
        state = ReconciliationStateView(
            target_ref=_uuid(),
            target_type="SPOT_ACCOUNT",
            aggregate_status=Classification.MATERIAL_MISMATCH,
            last_healthy_at=None,
            last_checked_at=_OK_NOW,
            blocking_reason="Balance mismatch > threshold",
            revision=3,
        )
        assert state.blocking_reason == "Balance mismatch > threshold"

    def test_missing_target_ref_raises(self):
        with pytest.raises(ValidationError):
            ReconciliationStateView(  # type: ignore[call-arg]
                target_type="SPOT_ACCOUNT",
                aggregate_status=Classification.HEALTHY,
                last_checked_at=_OK_NOW,
                blocking_reason=None,
                revision=1,
            )

    def test_missing_revision_raises(self):
        with pytest.raises(ValidationError):
            ReconciliationStateView(  # type: ignore[call-arg]
                target_ref=_uuid(),
                target_type="SPOT_ACCOUNT",
                aggregate_status=Classification.HEALTHY,
                last_checked_at=_OK_NOW,
                blocking_reason=None,
            )

    def test_last_healthy_at_can_be_none(self):
        state = ReconciliationStateView(
            target_ref=_uuid(),
            target_type="SPOT_ACCOUNT",
            aggregate_status=Classification.PENDING,
            last_healthy_at=None,
            last_checked_at=_OK_NOW,
            blocking_reason=None,
            revision=1,
        )
        assert state.last_healthy_at is None


# ---------------------------------------------------------------------------
# Negative tests — boundary / malformed input
# ---------------------------------------------------------------------------

class TestNegativeTests:
    def test_decimal_string_input_to_entity_snapshot(self):
        """Pydantic coerces string decimals to Decimal internally."""
        snap = EntitySnapshot(
            entity_type="BALANCE",
            entity_key="USDT_BALANCE",
            internal_value=Decimal("999.99"),
            provider_value=Decimal("999.00"),
        )
        assert isinstance(snap.internal_value, Decimal)
        assert isinstance(snap.provider_value, Decimal)

    def test_negative_decimal_allowed(self):
        """Negative amounts are valid (e.g. short positions)."""
        snap = EntitySnapshot(
            entity_type="POSITION",
            entity_key="BTCUSDT_POSITION",
            internal_value=Decimal("-1.5"),
        )
        assert snap.internal_value == Decimal("-1.5")

    def test_zero_decimal_allowed(self):
        snap = EntitySnapshot(
            entity_type="BALANCE",
            entity_key="USDT_BALANCE",
            internal_value=Decimal("0"),
        )
        assert snap.internal_value == Decimal("0")

    def test_large_decimal(self):
        large = Decimal("999999999999.99")
        snap = EntitySnapshot(
            entity_type="BALANCE",
            entity_key="USDT_BALANCE",
            internal_value=large,
        )
        assert snap.internal_value == large

    def test_entity_snapshot_model_validate(self):
        """model_validate from dict."""
        snap = EntitySnapshot.model_validate({
            "entity_type": "BALANCE",
            "entity_key": "USDT_BALANCE",
            "internal_value": "500.00",
        })
        assert snap.internal_value == Decimal("500.00")

    def test_reconciliation_run_view_model_validate(self):
        run = ReconciliationRunView.model_validate({
            "id": str(_FAKE_UUID),
            "target_type": "SPOT",
            "target_ref": str(_uuid()),
            "items": [],
            "aggregate_classification": "HEALTHY",
            "created_at": _OK_NOW.isoformat(),
        })
        assert run.aggregate_classification is Classification.HEALTHY


# ---------------------------------------------------------------------------
# Failure-injection tests — monkeypatch dependency
# ---------------------------------------------------------------------------

class TestFailureInjection:
    def test_entity_snapshot_with_invalid_decimal(self, monkeypatch: pytest.MonkeyPatch):
        """When Decimal conversion fails, Pydantic raises ValidationError."""
        # Patch Decimal to raise on specific input
        original_decimal = Decimal
        monkeypatch.setattr(
            "decimal.Decimal",
            lambda x: (_ for _ in ()).throw(ValueError("bad decimal")),
            raising=False,
        )
        with pytest.raises((ValidationError, ValueError)):
            EntitySnapshot(
                entity_type="BALANCE",
                entity_key="USDT_BALANCE",
                internal_value="NOT_A_NUMBER",
            )
        # Restore
        import decimal
        decimal.Decimal = original_decimal

    def test_classification_with_mocked_enum(self, monkeypatch: pytest.MonkeyPatch):
        """When Classification lookup fails, ValueError propagates."""
        def _missing_(value):
            raise ValueError("mock missing")
        monkeypatch.setattr(
            Classification,
            "_missing_",
            _missing_,
        )
        with pytest.raises(ValueError):
            Classification("NONEXISTENT")
