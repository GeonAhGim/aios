# mypy: disable-error-code="call-arg,list-item,arg-type"
"""Negative/validation tests for src/foundation/connections/contracts/v1.py.

Split from test_v1.py to keep each file under the 500-line warn threshold.
See test_v1.py for positive/boundary tests.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.connections.contracts.v1 import (
    AccountConnectionView,
    AccountSnapshotView,
    BeginConnectionRequest,
    ConnectionState,
    SnapshotValueView,
)

# ---------------------------------------------------------------------------
# BeginConnectionRequest - validation failures
# ---------------------------------------------------------------------------


class TestBeginConnectionRequestValidation:
    """Negative tests: field validation rules."""

    def test_missing_provider_code_raises(self) -> None:
        with pytest.raises(ValidationError):
            BeginConnectionRequest(
                opaque_account_ref="acc-1",
                requested_capability_profile=[],
            )

    def test_missing_account_ref_raises(self) -> None:
        with pytest.raises(ValidationError):
            BeginConnectionRequest(
                provider_code="BINANCE",
                requested_capability_profile=[],
            )

    def test_invalid_capability_raises(self) -> None:
        with pytest.raises(ValidationError):
            BeginConnectionRequest(
                provider_code="BINANCE",
                opaque_account_ref="acc-1",
                requested_capability_profile=["WRITE_ORDER"],
            )


# ---------------------------------------------------------------------------
# AccountConnectionView - validation failures
# ---------------------------------------------------------------------------


class TestAccountConnectionViewValidation:
    """Negative tests: field validation rules."""

    def test_missing_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            AccountConnectionView(
                provider_code="BINANCE",
                masked_account_label="****1234",
                state=ConnectionState.ACTIVE_READONLY,
                capability_profile=[],
                revision=1,
                created_at=datetime.now(timezone.utc),
            )

    def test_missing_provider_code_raises(self) -> None:
        with pytest.raises(ValidationError):
            AccountConnectionView(
                id=uuid4(),
                masked_account_label="****1234",
                state=ConnectionState.ACTIVE_READONLY,
                capability_profile=[],
                revision=1,
                created_at=datetime.now(timezone.utc),
            )

    def test_missing_masked_account_label_raises(self) -> None:
        with pytest.raises(ValidationError):
            AccountConnectionView(
                id=uuid4(),
                provider_code="BINANCE",
                state=ConnectionState.ACTIVE_READONLY,
                capability_profile=[],
                revision=1,
                created_at=datetime.now(timezone.utc),
            )

    def test_missing_state_raises(self) -> None:
        with pytest.raises(ValidationError):
            AccountConnectionView(
                id=uuid4(),
                provider_code="BINANCE",
                masked_account_label="****1234",
                capability_profile=[],
                revision=1,
                created_at=datetime.now(timezone.utc),
            )

    def test_missing_capability_profile_raises(self) -> None:
        with pytest.raises(ValidationError):
            AccountConnectionView(
                id=uuid4(),
                provider_code="BINANCE",
                masked_account_label="****1234",
                state=ConnectionState.ACTIVE_READONLY,
                revision=1,
                created_at=datetime.now(timezone.utc),
            )

    def test_missing_revision_raises(self) -> None:
        with pytest.raises(ValidationError):
            AccountConnectionView(
                id=uuid4(),
                provider_code="BINANCE",
                masked_account_label="****1234",
                state=ConnectionState.ACTIVE_READONLY,
                capability_profile=[],
                created_at=datetime.now(timezone.utc),
            )


# ---------------------------------------------------------------------------
# SnapshotValueView - validation failures
# ---------------------------------------------------------------------------


class TestSnapshotValueViewValidation:
    """Negative tests: field validation rules."""

    def test_missing_entity_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            SnapshotValueView(entity_key="BTC", value=Decimal("50000000"))

    def test_missing_entity_key_raises(self) -> None:
        with pytest.raises(ValidationError):
            SnapshotValueView(entity_type="ASSET", value=Decimal("50000000"))

    def test_missing_value_raises(self) -> None:
        with pytest.raises(ValidationError):
            SnapshotValueView(entity_type="ASSET", entity_key="BTC")


# ---------------------------------------------------------------------------
# AccountSnapshotView - validation failures
# ---------------------------------------------------------------------------


class TestAccountSnapshotViewValidation:
    """Negative tests: field validation rules."""

    def test_missing_connection_id_raises(self) -> None:
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            AccountSnapshotView(
                captured_at=now,
                provider_as_of=now,
                freshness="1m",
                currency="USD",
            )

    def test_missing_captured_at_raises(self) -> None:
        with pytest.raises(ValidationError):
            AccountSnapshotView(
                connection_id=uuid4(),
                provider_as_of=datetime.now(timezone.utc),
                freshness="1m",
                currency="USD",
            )

    def test_missing_provider_as_of_raises(self) -> None:
        with pytest.raises(ValidationError):
            AccountSnapshotView(
                connection_id=uuid4(),
                captured_at=datetime.now(timezone.utc),
                freshness="1m",
                currency="USD",
            )

    def test_missing_freshness_raises(self) -> None:
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            AccountSnapshotView(
                connection_id=uuid4(),
                captured_at=now,
                provider_as_of=now,
                currency="USD",
            )

    def test_missing_currency_raises(self) -> None:
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            AccountSnapshotView(
                connection_id=uuid4(),
                captured_at=now,
                provider_as_of=now,
                freshness="1m",
            )


# ---------------------------------------------------------------------------
# Failure injection: malformed data paths
# ---------------------------------------------------------------------------


class TestFailureInjection:
    """Failure-injection tests: malformed data that should raise."""

    def test_snapshot_value_with_non_numeric_value(self) -> None:
        """Decimal parsing must reject non-numeric strings."""
        from decimal import InvalidOperation

        with pytest.raises((ValidationError, InvalidOperation)):
            SnapshotValueView(
                entity_type="ASSET",
                entity_key="BTC",
                value=Decimal("not-a-number"),
            )

    def test_account_snapshot_with_invalid_values_list(self) -> None:
        """values list must only contain SnapshotValueView instances."""
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            AccountSnapshotView(
                connection_id=uuid4(),
                captured_at=now,
                provider_as_of=now,
                freshness="1m",
                currency="USD",
                values=[{"entity_type": "ASSET", "entity_key": "BTC"}],
            )

    def test_connection_view_with_invalid_state_type(self) -> None:
        """state must be a ConnectionState enum, not a raw string."""
        with pytest.raises(ValidationError):
            AccountConnectionView(
                id=uuid4(),
                provider_code="BINANCE",
                masked_account_label="****1234",
                state="ACTIVE",
                capability_profile=[],
                revision=1,
                created_at=datetime.now(timezone.utc),
            )
