"""Tests for src/foundation/connections/contracts/v1.py - Connected Asset contract v1.

DoD: negative tests >=3, failure-injection >=1, coverage >=70%.

D3 깊이 증빙(FA-8, FA-24, LA-1, LB-1, LC-1)의 gate_red/perf 테스트는
test_v1_depth.py에 있다(CLAUDE.md §12, loc_over_800 래칫 회피를 위한 책임별 분리).

Validation/negative tests are in test_v1_validation.py (LOC ratchet split).
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

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
# AccountConnectionView - valid construction
# ---------------------------------------------------------------------------


class TestAccountConnectionView:
    """Positive + boundary tests for AccountConnectionView."""

    def test_minimal_valid(self) -> None:
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
# SnapshotValueView - valid construction
# ---------------------------------------------------------------------------


class TestSnapshotValueView:
    """Positive + boundary tests for SnapshotValueView."""

    def test_minimal_valid(self) -> None:
        sv = SnapshotValueView(
            entity_type="ASSET",
            entity_key="BTC",
            value=Decimal("50000000"),
        )
        assert sv.entity_type == "ASSET"
        assert sv.entity_key == "BTC"
        assert sv.value == Decimal("50000000")

    def test_token_type(self) -> None:
        sv = SnapshotValueView(
            entity_type="TOKEN",
            entity_key="SMOL",
            value=Decimal("0.0000000001"),
        )
        assert sv.value == Decimal("0.0000000001")


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
