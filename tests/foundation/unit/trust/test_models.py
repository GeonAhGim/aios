"""Trust Core domain/models.py unit tests — pure value objects, no DB.

Covers construction, frozen immutability, enum validation, and equality for
Disclosure/Consent/Tenant/Membership and their state enums.
"""
import dataclasses
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.foundation.trust.domain.models import (
    Consent,
    ConsentState,
    Disclosure,
    Membership,
    MembershipRole,
    MembershipState,
    Tenant,
    TenantKind,
    TenantState,
)

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


def _disclosure(**overrides: object) -> Disclosure:
    fields = dict(
        id=uuid4(),
        purpose="terms_of_service",
        revision=1,
        content_hash="hash",
        published_at=NOW,
        retired_at=None,
    )
    fields.update(overrides)
    return Disclosure(**fields)  # type: ignore[arg-type]


def _consent(**overrides: object) -> Consent:
    fields = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        subject_id=uuid4(),
        purpose="terms_of_service",
        disclosure_id=uuid4(),
        disclosure_revision=1,
        state=ConsentState.ACTIVE,
        accepted_at=NOW,
        revoked_at=None,
        expires_at=None,
    )
    fields.update(overrides)
    return Consent(**fields)  # type: ignore[arg-type]


def _tenant(**overrides: object) -> Tenant:
    fields = dict(id=uuid4(), kind=TenantKind.PERSONAL, state=TenantState.ACTIVE, created_at=NOW)
    fields.update(overrides)
    return Tenant(**fields)  # type: ignore[arg-type]


def _membership(**overrides: object) -> Membership:
    fields = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        subject_id=uuid4(),
        role=MembershipRole.MEMBER,
        state=MembershipState.ACTIVE,
        revision=1,
        created_at=NOW,
    )
    fields.update(overrides)
    return Membership(**fields)  # type: ignore[arg-type]


# --- positive construction ---------------------------------------------------


def test_disclosure_constructs_with_expected_fields() -> None:
    d = _disclosure()
    assert d.purpose == "terms_of_service"
    assert d.retired_at is None


def test_consent_constructs_with_expected_fields() -> None:
    c = _consent()
    assert c.state is ConsentState.ACTIVE
    assert c.revoked_at is None


def test_tenant_constructs_with_expected_fields() -> None:
    t = _tenant()
    assert t.kind is TenantKind.PERSONAL
    assert t.state is TenantState.ACTIVE


def test_membership_constructs_with_expected_fields() -> None:
    m = _membership()
    assert m.role is MembershipRole.MEMBER
    assert m.state is MembershipState.ACTIVE


def test_dataclasses_with_equal_fields_compare_equal() -> None:
    shared_id = uuid4()
    a = _disclosure(id=shared_id)
    b = _disclosure(id=shared_id)
    assert a == b


# --- negative: frozen immutability -------------------------------------------


def test_disclosure_is_frozen_and_rejects_mutation() -> None:
    d = _disclosure()
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.purpose = "changed"  # type: ignore[misc]


def test_tenant_is_frozen_and_rejects_mutation() -> None:
    t = _tenant()
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.state = TenantState.SUSPENDED  # type: ignore[misc]


def test_membership_is_frozen_and_rejects_mutation() -> None:
    m = _membership()
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.role = MembershipRole.OWNER  # type: ignore[misc]


# --- negative: invalid construction ------------------------------------------


def test_membership_missing_required_field_raises_type_error() -> None:
    with pytest.raises(TypeError):
        Membership(  # type: ignore[call-arg]
            id=uuid4(),
            tenant_id=uuid4(),
            subject_id=uuid4(),
            role=MembershipRole.MEMBER,
            state=MembershipState.ACTIVE,
            # revision omitted, created_at omitted
        )


def test_consent_state_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        ConsentState("PENDING")


def test_tenant_kind_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        TenantKind("UNKNOWN")


# --- failure injection --------------------------------------------------------


def test_membership_role_construction_surfaces_injected_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate a broken enum value-lookup dependency (e.g. corrupted _value2member_map_)
    to verify enum construction fails closed rather than silently returning a bad member."""
    broken_map: dict[object, object] = {}
    monkeypatch.setattr(MembershipRole, "_value2member_map_", broken_map)

    with pytest.raises(ValueError):
        MembershipRole("MEMBER")


# --- performance ---------------------------------------------------------------


@pytest.mark.perf
def test_bulk_construction_meets_latency_budget() -> None:
    """10k instantiations of every value object must stay well under 1s (p50 budget
    for pure in-memory dataclass construction, ADR-2026-09-09-C Decision 1 default)."""
    start = time.perf_counter()
    for _ in range(10_000):
        _disclosure()
        _consent()
        _tenant()
        _membership()
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0
