"""Tests for src/api/schemas/portfolio.py — RebalanceAdjustmentRequest,
RebalanceRequest, to_adjustments.

Coverage target: 70 %+ on the module.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.api.schemas.portfolio import (
    RebalanceAdjustmentRequest,
    RebalanceRequest,
    to_adjustments,
)

# ------------------------------------------------------------------ #
#  RebalanceAdjustmentRequest — happy path                           #
# ------------------------------------------------------------------ #


class TestRebalanceAdjustmentRequestHappyPath:
    """Basic construction and validation."""

    def test_minimal_valid_construction(self):
        adj = RebalanceAdjustmentRequest(
            execution_id=1,
            new_allocated_capital=Decimal("1000.00"),
        )
        assert adj.execution_id == 1
        assert adj.new_allocated_capital == Decimal("1000.00")

    def test_model_dump(self):
        adj = RebalanceAdjustmentRequest(
            execution_id=42,
            new_allocated_capital=Decimal("9999.99"),
        )
        dumped = adj.model_dump()
        assert dumped == {
            "execution_id": 42,
            "new_allocated_capital": Decimal("9999.99"),
        }

    def test_model_dump_json(self):
        adj = RebalanceAdjustmentRequest(
            execution_id=7,
            new_allocated_capital=Decimal("0.01"),
        )
        json_str = adj.model_dump_json()
        assert "execution_id" in json_str
        assert "7" in json_str
        assert "new_allocated_capital" in json_str
        assert "0.01" in json_str


# ------------------------------------------------------------------ #
#  RebalanceAdjustmentRequest — negative tests (boundary / bad input) #
# ------------------------------------------------------------------ #


class TestRebalanceAdjustmentRequestNegative:
    """Boundary and invalid-input tests (negative tests #1-#3)."""

    def test_execution_id_must_be_int(self):
        """execution_id coerces to int; non-numeric str must fail."""
        with pytest.raises(ValidationError):
            RebalanceAdjustmentRequest(
                execution_id="not-an-int",
                new_allocated_capital=Decimal("100"),
            )

    def test_execution_id_negative_allowed_as_int(self):
        """Pydantic does not enforce non-negative by default; negative int
        passes type-check (application-level guard should handle this)."""
        adj = RebalanceAdjustmentRequest(
            execution_id=-1,
            new_allocated_capital=Decimal("100"),
        )
        assert adj.execution_id == -1

    def test_new_allocated_capital_zero_allowed(self):
        """Zero capital is a valid Decimal value at schema level."""
        adj = RebalanceAdjustmentRequest(
            execution_id=1,
            new_allocated_capital=Decimal("0"),
        )
        assert adj.new_allocated_capital == Decimal("0")


# ------------------------------------------------------------------ #
#  RebalanceRequest — happy path                                     #
# ------------------------------------------------------------------ #


class TestRebalanceRequestHappyPath:
    """Basic construction of RebalanceRequest."""

    def test_minimal_valid_construction(self):
        req = RebalanceRequest(adjustments=[])
        assert req.adjustments == []

    def test_with_one_adjustment(self):
        adj = RebalanceAdjustmentRequest(
            execution_id=1,
            new_allocated_capital=Decimal("5000"),
        )
        req = RebalanceRequest(adjustments=[adj])
        assert len(req.adjustments) == 1
        assert req.adjustments[0].execution_id == 1

    def test_with_multiple_adjustments(self):
        adjustments = [
            RebalanceAdjustmentRequest(
                execution_id=i,
                new_allocated_capital=Decimal(str(i * 100)),
            )
            for i in range(1, 4)
        ]
        req = RebalanceRequest(adjustments=adjustments)
        assert len(req.adjustments) == 3

    def test_model_dump(self):
        adj = RebalanceAdjustmentRequest(
            execution_id=99,
            new_allocated_capital=Decimal("100000"),
        )
        req = RebalanceRequest(adjustments=[adj])
        dumped = req.model_dump()
        assert isinstance(dumped["adjustments"], list)
        assert dumped["adjustments"][0]["execution_id"] == 99


# ------------------------------------------------------------------ #
#  RebalanceRequest — negative tests                                  #
# ------------------------------------------------------------------ #


class TestRebalanceRequestNegative:
    """Negative tests (#4-#5)."""

    def test_adjustments_must_be_list_of_valid_items(self):
        """An invalid item inside adjustments list must raise."""
        with pytest.raises(ValidationError):
            RebalanceRequest(
                adjustments=[{"execution_id": "bad", "new_allocated_capital": "100"}],
            )

    def test_model_dump_json_roundtrip(self):
        adj = RebalanceAdjustmentRequest(
            execution_id=5,
            new_allocated_capital=Decimal("250.50"),
        )
        req = RebalanceRequest(adjustments=[adj])
        json_str = req.model_dump_json()
        rebuilt = RebalanceRequest.model_validate_json(json_str)
        assert rebuilt.adjustments[0].execution_id == 5
        assert rebuilt.adjustments[0].new_allocated_capital == Decimal("250.50")


# ------------------------------------------------------------------ #
#  to_adjustments — happy path                                        #
# ------------------------------------------------------------------ #


class TestToAdjustmentsHappyPath:
    """Conversion from domain DTOs to schema models.

    Note: to_adjustments expects a RebalanceRequest (schema), not a plain list.
    """

    def test_empty_list(self):
        req = RebalanceRequest(adjustments=[])
        result = to_adjustments(req)
        assert result == []

    def test_single_item(self):
        schema_adj = RebalanceAdjustmentRequest(
            execution_id=10,
            new_allocated_capital=Decimal("3000.00"),
        )
        req = RebalanceRequest(adjustments=[schema_adj])
        result = to_adjustments(req)
        assert len(result) == 1
        assert result[0].execution_id == 10
        assert result[0].new_allocated_capital == Decimal("3000.00")

    def test_multiple_items(self):
        schema_adjustments = [
            RebalanceAdjustmentRequest(
                execution_id=i,
                new_allocated_capital=Decimal(str(i * 500)),
            )
            for i in range(1, 6)
        ]
        req = RebalanceRequest(adjustments=schema_adjustments)
        result = to_adjustments(req)
        assert len(result) == 5
        for i, item in enumerate(result, start=1):
            assert item.execution_id == i
            assert item.new_allocated_capital == Decimal(str(i * 500))


# ------------------------------------------------------------------ #
#  to_adjustments — negative / failure-injection tests                #
# ------------------------------------------------------------------ #


class TestToAdjustmentsNegative:
    """Negative tests (#6) and failure-injection test (#7).

    Note: to_adjustments expects a RebalanceRequest, not a plain list.
    """

    def test_domain_item_with_zero_capital(self):
        """Zero capital should pass through to_adjustments."""
        schema_adj = RebalanceAdjustmentRequest(
            execution_id=999,
            new_allocated_capital=Decimal("0"),
        )
        req = RebalanceRequest(adjustments=[schema_adj])
        result = to_adjustments(req)
        assert result[0].new_allocated_capital == Decimal("0")

    def test_domain_item_with_negative_capital(self):
        """Negative capital passes through (schema doesn't enforce sign)."""
        schema_adj = RebalanceAdjustmentRequest(
            execution_id=1,
            new_allocated_capital=Decimal("-100"),
        )
        req = RebalanceRequest(adjustments=[schema_adj])
        result = to_adjustments(req)
        assert result[0].new_allocated_capital == Decimal("-100")

    def test_large_execution_id(self):
        """Very large int should be handled without overflow."""
        schema_adj = RebalanceAdjustmentRequest(
            execution_id=2**31 - 1,
            new_allocated_capital=Decimal("999999999.99"),
        )
        req = RebalanceRequest(adjustments=[schema_adj])
        result = to_adjustments(req)
        assert result[0].execution_id == 2**31 - 1

    def test_failure_injection_domain_exception(self):
        """to_adjustments accesses domain attributes — if a domain item raises
        on attribute access, the exception should propagate (not be swallowed).

        We bypass Pydantic validation by using object.__setattr__ on a
        RebalanceRequest created via model_construct (bypasses validation).
        """

        class BrokenDomainItem:
            execution_id = 1

            @property
            def new_allocated_capital(self):
                raise RuntimeError("injected domain failure")

        req = RebalanceRequest.model_construct(adjustments=[BrokenDomainItem()])
        with pytest.raises(RuntimeError, match="injected domain failure"):
            to_adjustments(req)


# ------------------------------------------------------------------ #
#  Decimal precision edge cases                                       #
# ------------------------------------------------------------------ #


class TestDecimalPrecision:
    """Ensure Decimal precision is preserved through the schema layer."""

    def test_high_precision_decimal(self):
        """Many decimal places should be preserved exactly."""
        adj = RebalanceAdjustmentRequest(
            execution_id=1,
            new_allocated_capital=Decimal("123456789.12345678901234567890"),
        )
        assert adj.new_allocated_capital == Decimal("123456789.12345678901234567890")

    def test_scientific_notation_input(self):
        """Scientific notation Decimal should be accepted."""
        adj = RebalanceAdjustmentRequest(
            execution_id=1,
            new_allocated_capital=Decimal("1E+2"),
        )
        assert adj.new_allocated_capital == Decimal("100")
