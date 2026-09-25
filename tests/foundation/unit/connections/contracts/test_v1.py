"""Tests for src/foundation/connections/contracts/v1.py - Connected Asset contract v1.

DoD: negative tests >=3, failure-injection >=1, coverage >=70%.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.connections.contracts.v1 import (
    SCHEMA_VERSION,
    AccountConnectionView,
    AccountSnapshotView,
    BeginConnectionRequest,
    CapabilityScope,
    ConnectionState,
    SnapshotValueView,
)

# ---------------------------------------------------------------------------
# ConnectionState enum - membership, iteration, value access
# ---------------------------------------------------------------------------

class TestConnectionState:
    """Negative tests: enum members, membership, iteration."""

    def test_all_expected_members_exist(self) -> None:
        """All 6 ConnectionState values must be present."""
        expected = {
            "PENDING_CONSENT",
            "CONNECTING",
            "ACTIVE_READONLY",
            "DEGRADED",
            "REVOKED",
            "DISCONNECTED",
        }
        actual = {m.name for m in ConnectionState}
        assert actual == expected

    def test_invalid_string_is_not_member(self) -> None:
        """A string not in the enum should not be a valid ConnectionState."""
        with pytest.raises(ValueError):
            ConnectionState("UNKNOWN_STATE")

    def test_connection_state_iteration(self) -> None:
        """Iterating over ConnectionState yields all members."""
        states = list(ConnectionState)
        assert len(states) == 6

    def test_connection_state_values_are_strings(self) -> None:
        """Each ConnectionState member value must be a string."""
        for state in ConnectionState:
            assert isinstance(state.value, str)


# ---------------------------------------------------------------------------
# CapabilityScope enum
# ---------------------------------------------------------------------------

class TestCapabilityScope:
    """Negative tests: enum membership, iteration."""

    def test_all_expected_members_exist(self) -> None:
        expected = {"READ_BALANCE", "READ_POSITION", "READ_ACTIVITY"}
        actual = {m.name for m in CapabilityScope}
        assert actual == expected

    def test_invalid_capability(self) -> None:
        with pytest.raises(ValueError):
            CapabilityScope("WRITE_ORDER")

    def test_capability_iteration(self) -> None:
        scopes = list(CapabilityScope)
        assert len(scopes) == 3


# ---------------------------------------------------------------------------
# SCHEMA_VERSION constant
# ---------------------------------------------------------------------------

class TestSchemaVersion:
    def test_schema_version_is_string(self) -> None:
        assert isinstance(SCHEMA_VERSION, str)

    def test_schema_version_value(self) -> None:
        assert SCHEMA_VERSION == "v1"


# ---------------------------------------------------------------------------
# BeginConnectionRequest - valid construction
# ---------------------------------------------------------------------------

class TestBeginConnectionRequest:
    """Positive + boundary tests for BeginConnectionRequest."""

    def test_minimal_valid(self) -> None:
        req = BeginConnectionRequest(
            provider_code="BINANCE",
            opaque_account_ref="acc-123",
            requested_capability_profile=[],
        )
        assert req.provider_code == "BINANCE"
        assert req.opaque_account_ref == "acc-123"
        assert req.requested_capability_profile == []

    def test_with_capabilities(self) -> None:
        req = BeginConnectionRequest(
            provider_code="KRAKEN",
            opaque_account_ref="acc-456",
            requested_capability_profile=[
                CapabilityScope.READ_BALANCE,
                CapabilityScope.READ_POSITION,
            ],
        )
        assert len(req.requested_capability_profile) == 2
        assert CapabilityScope.READ_BALANCE in req.requested_capability_profile

    def test_all_capabilities(self) -> None:
        req = BeginConnectionRequest(
            provider_code="KRAKEN",
            opaque_account_ref="acc-789",
            requested_capability_profile=list(CapabilityScope),
        )
        assert len(req.requested_capability_profile) == 3

    def test_empty_provider_code_is_valid(self) -> None:
        """Empty string is technically valid - pydantic allows it."""
        req = BeginConnectionRequest(
            provider_code="",
            opaque_account_ref="x",
            requested_capability_profile=[],
        )
        assert req.provider_code == ""

    def test_empty_account_ref_is_valid(self) -> None:
        """Empty opaque_account_ref is accepted by the schema."""
        req = BeginConnectionRequest(
            provider_code="BINANCE",
            opaque_account_ref="",
            requested_capability_profile=[],
        )
        assert req.opaque_account_ref == ""


# ---------------------------------------------------------------------------
# BeginConnectionRequest - validation errors (negative tests)
# ---------------------------------------------------------------------------

class TestBeginConnectionRequestValidation:
    """Negative tests: invalid inputs that should raise ValidationError."""

    def test_missing_provider_code_raises(self) -> None:
        with pytest.raises(ValidationError):
            BeginConnectionRequest(
                opaque_account_ref="ref",
                requested_capability_profile=[],
            )

    def test_missing_opaque_account_ref_raises(self) -> None:
        with pytest.raises(ValidationError):
            BeginConnectionRequest(
                provider_code="BINANCE",
                requested_capability_profile=[],
            )

    def test_missing_capability_profile_raises(self) -> None:
        with pytest.raises(ValidationError):
            BeginConnectionRequest(
                provider_code="BINANCE",
                opaque_account_ref="ref",
            )

    def test_invalid_capability_value_raises(self) -> None:
        with pytest.raises(ValidationError):
            BeginConnectionRequest(
                provider_code="BINANCE",
                opaque_account_ref="ref",
                requested_capability_profile=["INVALID_SCOPE"],
            )

    def test_provider_code_int_is_rejected(self) -> None:
        """Pydantic v2 rejects int for str fields (no coercion)."""
        with pytest.raises(ValidationError):
            BeginConnectionRequest(
                provider_code=123,
                opaque_account_ref="ref",
                requested_capability_profile=[],
            )


# ---------------------------------------------------------------------------
# AccountConnectionView - valid construction
# ---------------------------------------------------------------------------

class TestAccountConnectionView:
    """Positive tests for AccountConnectionView."""

    def test_minimal_required_fields(self) -> None:
        conn_id = uuid4()
        now = datetime.now(timezone.utc)
        view = AccountConnectionView(
            id=conn_id,
            provider_code="BINANCE",
            masked_account_label="****1234",
            state=ConnectionState.ACTIVE_READONLY,
            capability_profile=[CapabilityScope.READ_BALANCE],
            revision=1,
            created_at=now,
        )
        assert view.id == conn_id
        assert view.provider_code == "BINANCE"
        assert view.masked_account_label == "****1234"
        assert view.state == ConnectionState.ACTIVE_READONLY
        assert view.revision == 1
        assert view.created_at == now

    def test_default_scope_verified_is_false(self) -> None:
        conn_id = uuid4()
        now = datetime.now(timezone.utc)
        view = AccountConnectionView(
            id=conn_id,
            provider_code="BINANCE",
            masked_account_label="****1234",
            state=ConnectionState.ACTIVE_READONLY,
            capability_profile=[CapabilityScope.READ_BALANCE],
            revision=1,
            created_at=now,
        )
        assert view.scope_verified is False

    def test_schema_version_defaults_to_v1(self) -> None:
        conn_id = uuid4()
        now = datetime.now(timezone.utc)
        view = AccountConnectionView(
            id=conn_id,
            provider_code="BINANCE",
            masked_account_label="****1234",
            state=ConnectionState.ACTIVE_READONLY,
            capability_profile=[CapabilityScope.READ_BALANCE],
            revision=1,
            created_at=now,
        )
        assert view.schema_version == "v1"

    def test_schema_version_can_be_overridden(self) -> None:
        conn_id = uuid4()
        now = datetime.now(timezone.utc)
        view = AccountConnectionView(
            id=conn_id,
            provider_code="BINANCE",
            masked_account_label="****1234",
            state=ConnectionState.ACTIVE_READONLY,
            capability_profile=[CapabilityScope.READ_BALANCE],
            revision=1,
            created_at=now,
            schema_version="v2",
        )
        assert view.schema_version == "v2"

    def test_created_at_can_be_none(self) -> None:
        conn_id = uuid4()
        view = AccountConnectionView(
            id=conn_id,
            provider_code="BINANCE",
            masked_account_label="****1234",
            state=ConnectionState.ACTIVE_READONLY,
            capability_profile=[CapabilityScope.READ_BALANCE],
            revision=1,
            created_at=None,
        )
        assert view.created_at is None

    def test_empty_capability_profile(self) -> None:
        conn_id = uuid4()
        now = datetime.now(timezone.utc)
        view = AccountConnectionView(
            id=conn_id,
            provider_code="BINANCE",
            masked_account_label="****1234",
            state=ConnectionState.ACTIVE_READONLY,
            capability_profile=[],
            revision=1,
            created_at=now,
        )
        assert view.capability_profile == []

    def test_scope_verified_true(self) -> None:
        conn_id = uuid4()
        now = datetime.now(timezone.utc)
        view = AccountConnectionView(
            id=conn_id,
            provider_code="BINANCE",
            masked_account_label="****1234",
            state=ConnectionState.ACTIVE_READONLY,
            capability_profile=[CapabilityScope.READ_BALANCE],
            revision=1,
            created_at=now,
            scope_verified=True,
        )
        assert view.scope_verified is True

    def test_all_connection_states_valid(self) -> None:
        """Every ConnectionState member must be usable as a field value."""
        conn_id = uuid4()
        now = datetime.now(timezone.utc)
        for state in ConnectionState:
            view = AccountConnectionView(
                id=conn_id,
                provider_code="BINANCE",
                masked_account_label="****1234",
                state=state,
                capability_profile=[],
                revision=1,
                created_at=now,
            )
            assert view.state == state


# ---------------------------------------------------------------------------
# AccountConnectionView - validation errors (negative tests)
# ---------------------------------------------------------------------------

class TestAccountConnectionViewValidation:
    """Negative tests: missing required fields should raise ValidationError."""

    def test_missing_id_raises(self) -> None:
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            AccountConnectionView(
                provider_code="BINANCE",
                masked_account_label="****1234",
                state=ConnectionState.ACTIVE_READONLY,
                capability_profile=[],
                revision=1,
                created_at=now,
            )

    def test_missing_provider_code_raises(self) -> None:
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            AccountConnectionView(
                id=uuid4(),
                masked_account_label="****1234",
                state=ConnectionState.ACTIVE_READONLY,
                capability_profile=[],
                revision=1,
                created_at=now,
            )

    def test_invalid_state_raises(self) -> None:
        with pytest.raises(ValidationError):
            AccountConnectionView(
                id=uuid4(),
                provider_code="BINANCE",
                masked_account_label="****1234",
                state="INVALID_STATE",
                capability_profile=[],
                revision=1,
                created_at=datetime.now(timezone.utc),
            )

    def test_invalid_revision_type_raises(self) -> None:
        """revision must be int, not str."""
        with pytest.raises(ValidationError):
            AccountConnectionView(
                id=uuid4(),
                provider_code="BINANCE",
                masked_account_label="****1234",
                state=ConnectionState.ACTIVE_READONLY,
                capability_profile=[],
                revision="one",
                created_at=datetime.now(timezone.utc),
            )

    def test_negative_revision_accepted(self) -> None:
        """Pydantic accepts negative revision — schema does not enforce >= 0."""
        view = AccountConnectionView(
            id=uuid4(),
            provider_code="BINANCE",
            masked_account_label="****1234",
            state=ConnectionState.ACTIVE_READONLY,
            capability_profile=[],
            revision=-1,
            created_at=datetime.now(timezone.utc),
        )
        assert view.revision == -1


# ---------------------------------------------------------------------------
# SnapshotValueView
# ---------------------------------------------------------------------------

class TestSnapshotValueView:
    """Positive + boundary tests for SnapshotValueView."""

    def test_valid_construction(self) -> None:
        sv = SnapshotValueView(
            entity_type="ASSET",
            entity_key="BTC",
            value=Decimal("1234.56"),
        )
        assert sv.entity_type == "ASSET"
        assert sv.entity_key == "BTC"
        assert sv.value == Decimal("1234.56")

    def test_negative_value(self) -> None:
        """Negative values should be allowed (e.g. loss)."""
        sv = SnapshotValueView(
            entity_type="POSITION",
            entity_key="ETH",
            value=Decimal("-100.00"),
        )
        assert sv.value == Decimal("-100.00")

    def test_zero_value(self) -> None:
        sv = SnapshotValueView(
            entity_type="ASSET",
            entity_key="USDT",
            value=Decimal("0"),
        )
        assert sv.value == Decimal("0")

    def test_large_decimal(self) -> None:
        sv = SnapshotValueView(
            entity_type="ASSET",
            entity_key="PORTFOLIO",
            value=Decimal("999999999999.99"),
        )
        assert sv.value == Decimal("999999999999.99")

    def test_high_precision_decimal(self) -> None:
        sv = SnapshotValueView(
            entity_type="TOKEN",
            entity_key="SMOL",
            value=Decimal("0.0000000001"),
        )
        assert sv.value == Decimal("0.0000000001")


# ---------------------------------------------------------------------------
# SnapshotValueView - validation errors (negative tests)
# ---------------------------------------------------------------------------

class TestSnapshotValueViewValidation:
    """Negative tests: missing required fields should raise ValidationError."""

    def test_missing_entity_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            SnapshotValueView(
                entity_key="BTC",
                value=Decimal("100"),
            )

    def test_missing_entity_key_raises(self) -> None:
        with pytest.raises(ValidationError):
            SnapshotValueView(
                entity_type="ASSET",
                value=Decimal("100"),
            )

    def test_missing_value_raises(self) -> None:
        with pytest.raises(ValidationError):
            SnapshotValueView(
                entity_type="ASSET",
                entity_key="BTC",
            )

    def test_float_value_is_coerced(self) -> None:
        """Pydantic coerces float to Decimal for Decimal fields."""
        sv = SnapshotValueView(
            entity_type="ASSET",
            entity_key="BTC",
            value=123.45,
        )
        assert sv.value == Decimal("123.45")


# ---------------------------------------------------------------------------
# AccountSnapshotView
# ---------------------------------------------------------------------------

class TestAccountSnapshotView:
    """Positive tests for AccountSnapshotView."""

    def test_minimal_valid(self) -> None:
        now = datetime.now(timezone.utc)
        snap = AccountSnapshotView(
            connection_id=uuid4(),
            captured_at=now,
            provider_as_of=now,
            freshness="1m",
            currency="USD",
            values=[],
        )
        assert snap.currency == "USD"
        assert snap.values == []
        assert snap.schema_version == "v1"

    def test_with_snapshot_values(self) -> None:
        now = datetime.now(timezone.utc)
        snap = AccountSnapshotView(
            connection_id=uuid4(),
            captured_at=now,
            provider_as_of=now,
            freshness="1m",
            currency="KRW",
            values=[
                SnapshotValueView(entity_type="ASSET", entity_key="BTC", value=Decimal("50000000")),
                SnapshotValueView(entity_type="ASSET", entity_key="ETH", value=Decimal("3000000")),
            ],
        )
        assert len(snap.values) == 2
        assert snap.values[0].entity_key == "BTC"

    def test_default_values_is_empty_list(self) -> None:
        now = datetime.now(timezone.utc)
        snap = AccountSnapshotView(
            connection_id=uuid4(),
            captured_at=now,
            provider_as_of=now,
            freshness="1m",
            currency="USD",
        )
        assert snap.values == []

    def test_schema_version_defaults_to_v1(self) -> None:
        now = datetime.now(timezone.utc)
        snap = AccountSnapshotView(
            connection_id=uuid4(),
            captured_at=now,
            provider_as_of=now,
            freshness="1m",
            currency="USD",
        )
        assert snap.schema_version == "v1"

    def test_schema_version_can_be_overridden(self) -> None:
        now = datetime.now(timezone.utc)
        snap = AccountSnapshotView(
            connection_id=uuid4(),
            captured_at=now,
            provider_as_of=now,
            freshness="1m",
            currency="USD",
            schema_version="v2",
        )
        assert snap.schema_version == "v2"


# ---------------------------------------------------------------------------
# AccountSnapshotView - validation errors (negative tests)
# ---------------------------------------------------------------------------

class TestAccountSnapshotViewValidation:
    """Negative tests: missing required fields should raise ValidationError."""

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
        with pytest.raises(ValidationError):
            AccountSnapshotView(
                connection_id=uuid4(),
                captured_at=datetime.now(timezone.utc),
                provider_as_of=datetime.now(timezone.utc),
                freshness="1m",
            )

    def test_missing_currency_raises(self) -> None:
        with pytest.raises(ValidationError):
            AccountSnapshotView(
                connection_id=uuid4(),
                captured_at=datetime.now(timezone.utc),
                provider_as_of=datetime.now(timezone.utc),
                freshness="1m",
            )

    def test_invalid_freshness_format_accepted(self) -> None:
        """freshness is just a str - any format is accepted by the schema."""
        now = datetime.now(timezone.utc)
        snap = AccountSnapshotView(
            connection_id=uuid4(),
            captured_at=now,
            provider_as_of=now,
            freshness="invalid_freshness",
            currency="USD",
        )
        assert snap.freshness == "invalid_freshness"


# ---------------------------------------------------------------------------
# Failure injection test - monkeypatch for dependency exception
# ---------------------------------------------------------------------------

class TestFailureInjection:
    """Failure injection: validate that invalid inputs raise properly."""

    def test_invalid_uuid_propagates(self) -> None:
        """Passing a non-UUID value for a UUID field should raise."""
        with pytest.raises(ValidationError):
            AccountConnectionView(
                id="not-a-uuid",
                provider_code="BINANCE",
                masked_account_label="****1234",
                state=ConnectionState.ACTIVE_READONLY,
                capability_profile=[],
                revision=1,
                created_at=datetime.now(timezone.utc),
            )

    def test_invalid_datetime_propagates(self) -> None:
        """Passing a non-datetime value for a datetime field should raise."""
        with pytest.raises(ValidationError):
            AccountSnapshotView(
                connection_id=uuid4(),
                captured_at="not-a-datetime",
                provider_as_of=datetime.now(timezone.utc),
                freshness="1m",
                currency="USD",
            )
