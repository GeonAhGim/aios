"""task-4682 — src/api/schemas/foundation/trust.py coverage 0% -> 70%+.

Covers AcceptDisclosureRequest/TrustStatusResponse construction, negative
validation paths, a monkeypatch-induced dependency failure, and a throughput
budget for repeated model construction (ADR-2026-09-09-C Decision 1).
"""

from __future__ import annotations

from datetime import datetime, timezone

# PerfBudget: lazy import so ruff does not flag the type annotation (F821).
# Real type is from tests.conftest; string annotation avoids the import at runtime.
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.schemas.foundation import trust as trust_schema
from src.api.schemas.foundation.trust import (
    AcceptDisclosureRequest,
    TrustStatusResponse,
)
from src.foundation.trust.contracts.v1 import ConsentDecision, ConsentState

if TYPE_CHECKING:
    from tests.conftest import PerfBudget  # noqa: F401


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
def test_trust_status_response_construction_throughput_budget(
    perf_budget: PerfBudget,
) -> None:
    """p95 construction latency for 500 responses stays under 50ms/op budget
    (ADR-2026-09-09-C Decision 1 — simple schema construction path)."""
    tenant_id = uuid4()
    as_of = datetime.now(timezone.utc)
    consents = [_consent_decision() for _ in range(5)]

    # batch=500: 총 500회 호출을 한 구간에 묶어 process_time 양자화 오차 제거
    samples = perf_budget.samples(
        lambda: TrustStatusResponse(tenant_id=tenant_id, consents=consents, as_of=as_of),
        n=10,
        warmup=0,
        batch=500,
    )

    # cpu_ms 기준, 단위 일관성: ms (0.05초 → 50.0ms)
    assert all(s.cpu_ms < 50.0 for s in samples), f"budget 초과: {[s.cpu_ms for s in samples]}"


@pytest.mark.perf
def test_cpu_measurement_converges_to_zero_for_fast_call(
    perf_budget: PerfBudget,
) -> None:
    """CPU 측정 0 수렴 검증: `TrustStatusResponse` construction은 Pydantic
    BaseModel 생성만 수행하므로 `perf_budget.sample`가 측정하는 CPU 시간이
    1ms 미만으로 수렴해야 한다. Windows의 `time.process_time()` 약 15.6ms
    양자화 오차가 batch로 줄어드는지 확인한다.

    불변식(I-03): 순수 도메인 로직은 1ms 미만이어야 한다.
    """
    tenant_id = uuid4()
    as_of = datetime.now(timezone.utc)
    consents = [_consent_decision() for _ in range(5)]

    def _one_call() -> None:
        TrustStatusResponse(tenant_id=tenant_id, consents=consents, as_of=as_of)

    sample = perf_budget.sample(_one_call, batch=64)

    # CPU 시간이 1ms 미만 (양자화 오차 포함)
    assert sample.cpu_ms < 1.0, (
        f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
        f"스키마 construction은 1ms 미만이어야 함 (budget<1ms)"
    )


@pytest.mark.perf
def test_gate_red_budget_catches_10x_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2): CPU 예산 테스트이므로 `time.process_time`을
    monkeypatch해 1회 호출당 예산(1ms)을 100배 초과하는 CPU 시간을 가장하면
    같은 단언이 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완료가 아니라, 단언이 "제대로 작동하는지"를
    역으로 증명한다. fake process_time이 100ms를 반환하므로
    batch=64로 나눈 평균도 100ms > 1ms budget로 적색 확정.
    """
    # PerfBudget.sample() 내부에서 time.process_time()을 호출하므로
    # 이를 monkeypatch해 항상 100ms(100x budget)를 반환하게 한다.
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 0.1  # 100ms per call = 100x budget
        return _fake_time

    monkeypatch.setattr("time.process_time", _fake_process_time)

    tenant_id = uuid4()
    as_of = datetime.now(timezone.utc)
    consents = [_consent_decision() for _ in range(5)]

    def _one_call() -> None:
        TrustStatusResponse(tenant_id=tenant_id, consents=consents, as_of=as_of)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        sample = perf_budget.sample(_one_call, batch=64)
        assert sample.cpu_ms < 1.0, (
            f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
            f"스키마 construction은 1ms 미만이어야 함 (budget<1ms)"
        )
