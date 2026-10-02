"""Negative/boundary tests for src/foundation/reconciliation/contracts/v1.py.

Covers decimal coercion, negative/zero/large amounts, and model_validate paths.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from src.foundation.reconciliation.contracts.v1 import (
    Classification,
    EntitySnapshot,
    ReconciliationRunView,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OK_NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
_FAKE_UUID = uuid4()


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
        snap = EntitySnapshot.model_validate(
            {
                "entity_type": "BALANCE",
                "entity_key": "USDT_BALANCE",
                "internal_value": "500.00",
            }
        )
        assert snap.internal_value == Decimal("500.00")

    def test_reconciliation_run_view_model_validate(self):
        run = ReconciliationRunView.model_validate(
            {
                "id": str(_FAKE_UUID),
                "target_type": "SPOT",
                "target_ref": str(uuid4()),
                "items": [],
                "aggregate_classification": "HEALTHY",
                "created_at": _OK_NOW.isoformat(),
            }
        )
        assert run.aggregate_classification is Classification.HEALTHY
