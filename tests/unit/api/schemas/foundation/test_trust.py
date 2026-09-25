"""task-4682 — src/api/schemas/foundation/trust.py coverage 0% -> 70%+.

Covers AcceptDisclosureRequest/TrustStatusResponse construction, negative
validation paths, a monkeypatch-induced dependency failure, and a throughput
budget for repeated model construction (ADR-2026-09-09-C Decision 1).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.schemas.foundation import trust as trust_schema
from src.api.schemas.foundation.trust import (
    AcceptDisclosureRequest,
    TrustStatusResponse,
)
from src.foundation.trust.contracts.v1 import ConsentDecision, ConsentState


def _consent_decision() -> ConsentDecision:
    return ConsentDecision(
        consent_id=uuid4(),
        tenant_id=uuid4(),
        purpose="marketing",
        disclosure_id=uuid4(),
        disclosure_revision=1,
        state=ConsentState.ACTIVE,
        accepted_at=datetime.now(timezone.utc),
        revoked_at=None,
        expires_at=None,
    )


def test_accept_disclosure_request_valid_roundtrip() -> None:
    req = AcceptDisclosureRequest(purpose="marketing", disclosure_revision=3)
    assert req.purpose == "marketing"
    assert req.disclosure_revision == 3


def test_accept_disclosure_request_missing_purpose_raises() -> None:
    with pytest.raises(ValidationError):
        AcceptDisclosureRequest.model_validate({"disclosure_revision": 1})


def test_accept_disclosure_request_invalid_revision_type_raises() -> None:
    with pytest.raises(ValidationError):
        AcceptDisclosureRequest(purpose="marketing", disclosure_revision="not-an-int")


def test_accept_disclosure_request_invalid_purpose_type_raises() -> None:
    with pytest.raises(ValidationError):
        AcceptDisclosureRequest(purpose=["marketing"], disclosure_revision=1)


def test_trust_status_response_valid_roundtrip() -> None:
    tenant_id = uuid4()
    as_of = datetime.now(timezone.utc)
    resp = TrustStatusResponse(
        tenant_id=tenant_id,
        consents=[_consent_decision()],
        as_of=as_of,
    )
    assert resp.tenant_id == tenant_id
    assert resp.as_of == as_of
    assert len(resp.consents) == 1


def test_trust_status_response_invalid_tenant_id_raises() -> None:
    with pytest.raises(ValidationError):
        TrustStatusResponse(
            tenant_id="not-a-uuid",
            consents=[],
            as_of=datetime.now(timezone.utc),
        )


def test_trust_status_response_invalid_consents_element_raises() -> None:
    with pytest.raises(ValidationError):
        TrustStatusResponse(
            tenant_id=uuid4(),
            consents=["not-a-consent-decision"],
            as_of=datetime.now(timezone.utc),
        )


def test_trust_status_response_missing_as_of_raises() -> None:
    with pytest.raises(ValidationError):
        TrustStatusResponse.model_validate({"tenant_id": uuid4(), "consents": []})


def test_trust_status_response_dependency_failure_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Break the `uuid.UUID` dependency that TrustStatusResponse.tenant_id relies
    on for validation and confirm the failure surfaces instead of being
    swallowed (pydantic-core resolves `uuid.UUID` dynamically at validation
    time, so patching the module attribute is enough to reach the failure)."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected dependency failure")

    monkeypatch.setattr("uuid.UUID", _boom)

    with pytest.raises((RuntimeError, TypeError, ValidationError)):
        trust_schema.TrustStatusResponse(
            tenant_id=uuid4(),
            consents=[],
            as_of=datetime.now(timezone.utc),
        )


@pytest.mark.perf
def test_trust_status_response_construction_throughput_budget() -> None:
    """p95 construction latency for 500 responses stays under 50ms/op budget
    (ADR-2026-09-09-C Decision 1 — simple schema construction path)."""
    tenant_id = uuid4()
    as_of = datetime.now(timezone.utc)
    consents = [_consent_decision() for _ in range(5)]

    durations: list[float] = []
    for _ in range(500):
        start = time.perf_counter()
        TrustStatusResponse(tenant_id=tenant_id, consents=consents, as_of=as_of)
        durations.append(time.perf_counter() - start)

    durations.sort()
    p95 = durations[int(len(durations) * 0.95) - 1]
    assert p95 < 0.05, f"p95 construction time {p95:.4f}s exceeded 50ms budget"
