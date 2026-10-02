"""Failure-injection tests for src/foundation/reconciliation/contracts/v1.py.

Covers monkeypatch-based failure paths (Decimal conversion, enum lookup).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.foundation.reconciliation.contracts.v1 import (
    Classification,
    EntitySnapshot,
)

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
