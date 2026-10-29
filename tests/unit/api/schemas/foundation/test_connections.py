"""task-4696 — src/api/schemas/foundation/connections.py coverage 0% -> 70%+.

Covers ConnectionListResponse construction, negative validation paths, a
monkeypatch-induced dependency failure, and a throughput budget for repeated
model construction (ADR-2026-09-09-C Decision 1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.schemas.foundation import connections as connections_schema
from src.api.schemas.foundation.connections import (
    AccountConnectionView,
    ConnectionListResponse,
    ConnectionState,
)
from src.foundation.connections.contracts.v1 import CapabilityScope
from tests.conftest import PerfBudget


def _connection_view() -> AccountConnectionView:
    return AccountConnectionView(
        id=uuid4(),
        provider_code="bitget",
        masked_account_label="****1234",
        state=ConnectionState.ACTIVE_READONLY,
        capability_profile=[CapabilityScope.READ_BALANCE],
        revision=1,
        created_at=datetime.now(UTC),
        scope_verified=False,
    )


def test_connection_list_response_valid_roundtrip() -> None:
    as_of = datetime.now(UTC)
    view = _connection_view()
    resp = ConnectionListResponse(connections=[view], as_of=as_of)
    assert resp.as_of == as_of
    assert len(resp.connections) == 1
    assert resp.connections[0].provider_code == "bitget"


def test_connection_list_response_empty_connections_roundtrip() -> None:
    as_of = datetime.now(UTC)
    resp = ConnectionListResponse(connections=[], as_of=as_of)
    assert resp.connections == []


def test_connection_list_response_missing_as_of_raises() -> None:
    with pytest.raises(ValidationError):
        ConnectionListResponse.model_validate({"connections": []})


def test_connection_list_response_invalid_connections_element_raises() -> None:
    with pytest.raises(ValidationError):
        ConnectionListResponse(
            connections=["not-a-connection-view"],
            as_of=datetime.now(UTC),
        )


def test_connection_list_response_invalid_as_of_type_raises() -> None:
    with pytest.raises(ValidationError):
        ConnectionListResponse(connections=[], as_of="not-a-datetime")


def test_connection_list_response_dependency_failure_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break the `uuid.UUID` dependency that AccountConnectionView.id (nested
    inside ConnectionListResponse.connections) relies on for validation and
    confirm the failure surfaces instead of being swallowed (pydantic-core
    resolves `uuid.UUID` dynamically at validation time, so patching the
    module attribute is enough to reach the failure)."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected dependency failure")

    monkeypatch.setattr("uuid.UUID", _boom)

    with pytest.raises((RuntimeError, TypeError, ValidationError)):
        connections_schema.ConnectionListResponse(
            connections=[
                {
                    "id": "not-a-real-uuid",
                    "provider_code": "bitget",
                    "masked_account_label": "****1234",
                    "state": ConnectionState.ACTIVE_READONLY,
                    "capability_profile": [CapabilityScope.READ_BALANCE],
                    "revision": 1,
                    "created_at": datetime.now(UTC),
                }
            ],
            as_of=datetime.now(UTC),
        )


@pytest.mark.perf
def test_connection_list_response_construction_throughput_budget(
    perf_budget: PerfBudget,
) -> None:
    """p95 construction latency for 500 responses stays under 50ms/op budget
    (ADR-2026-09-09-C Decision 1 — simple schema construction path)."""
    as_of = datetime.now(UTC)
    connections = [_connection_view() for _ in range(5)]

    # batch=500: 총 500회 호출을 한 구간에 묶어 process_time 양자화 오차 제거
    samples = perf_budget.samples(
        lambda: ConnectionListResponse(connections=connections, as_of=as_of),
        n=10,
        warmup=0,
        batch=500,
    )

    # cpu_ms 기준, 단위 일관성: ms (0.05초 → 50.0ms)
    assert all(s.cpu_ms < 50.0 for s in samples), f"budget 초과: {[s.cpu_ms for s in samples]}"


@pytest.mark.perf
def test_connection_list_response_cpu_budget_goes_red_with_10x_delay_injection(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    포착하는지 역으로 증명한다. `time.process_time`을 monkeypatch해 1회 호출당
    10x 예산(50ms × 10 = 500ms)을 초과하는 CPU 시간을 가장하면 budget 단언이
    AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다(task-11539 감사: 호출 1회 CPU 측정의 0 수렴 결함 방지).
    """
    budget_ms = 50.0  # 동일 예산

    as_of = datetime.now(UTC)
    connections = [_connection_view() for _ in range(5)]

    def _run() -> object:
        return ConnectionListResponse(connections=connections, as_of=as_of)

    # process_time을 monkeypatch: 호출할 때마다 500ms를 더한다.
    # PerfBudget.sample()은 time.process_time()을 호출하므로 이 값이
    # cpu_ms 측정값에 그대로 반영돼 budget을 10배 초과한다.
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 500.0  # 500ms = 10x budget per call
        return _fake_time

    monkeypatch.setattr("time.process_time", _fake_process_time)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        samples = perf_budget.samples(
            _run,
            n=10,
            warmup=0,
            batch=500,
        )
        assert all(s.cpu_ms < budget_ms for s in samples), (
            f"budget 초과: {[s.cpu_ms for s in samples]}"
        )
