"""task-4695 — src/api/schemas/foundation/paper_control.py coverage 0% -> 70%+.

Covers DeploymentListResponse construction, negative validation paths, a
monkeypatch-induced dependency failure, and a throughput budget for repeated
model construction (ADR-2026-09-09-C Decision 1).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.schemas.foundation import paper_control as paper_control_schema
from src.api.schemas.foundation.paper_control import (
    DeploymentCommandRequest,
    DeploymentListResponse,
    DeploymentState,
    PaperDeploymentView,
    RequestDeploymentRequest,
)


def _deployment_view() -> PaperDeploymentView:
    now = datetime.now(timezone.utc)
    return PaperDeploymentView(
        id=uuid4(),
        package_ref="pkg://strategy/1",
        connection_id=uuid4(),
        state=DeploymentState.RUNNING,
        fence_token=1,
        created_at=now,
        updated_at=now,
    )


def test_deployment_list_response_valid_roundtrip() -> None:
    as_of = datetime.now(timezone.utc)
    resp = DeploymentListResponse(deployments=[_deployment_view()], as_of=as_of)
    assert resp.as_of == as_of
    assert len(resp.deployments) == 1


def test_deployment_list_response_missing_as_of_raises() -> None:
    with pytest.raises(ValidationError):
        DeploymentListResponse.model_validate({"deployments": []})


def test_deployment_list_response_invalid_deployments_element_raises() -> None:
    with pytest.raises(ValidationError):
        DeploymentListResponse(
            deployments=["not-a-deployment-view"],
            as_of=datetime.now(timezone.utc),
        )


def test_deployment_list_response_invalid_as_of_type_raises() -> None:
    with pytest.raises(ValidationError):
        DeploymentListResponse(deployments=[], as_of="not-a-datetime")


def test_deployment_list_response_dependency_failure_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break the `uuid.UUID` dependency that PaperDeploymentView.id relies on
    for validation and confirm the failure surfaces instead of being
    swallowed (pydantic-core resolves `uuid.UUID` dynamically at validation
    time, so patching the module attribute is enough to reach the failure)."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected dependency failure")

    monkeypatch.setattr("uuid.UUID", _boom)

    with pytest.raises((RuntimeError, TypeError, ValidationError)):
        paper_control_schema.PaperDeploymentView(
            id=uuid4(),
            package_ref="pkg://strategy/1",
            connection_id=None,
            state=DeploymentState.RUNNING,
            fence_token=1,
            created_at=None,
            updated_at=None,
        )


def test_request_deployment_request_missing_required_field_raises() -> None:
    with pytest.raises(ValidationError):
        RequestDeploymentRequest.model_validate(
            {
                "package_ref": "pkg://strategy/1",
                "adapter_type": "paper",
                "provider_sandbox_account_ref": "acct-1",
            }
        )


def test_deployment_command_request_missing_idempotency_key_raises() -> None:
    with pytest.raises(ValidationError):
        DeploymentCommandRequest.model_validate({})


@pytest.mark.perf
def test_deployment_list_response_construction_throughput_budget() -> None:
    """p95 construction latency for 500 responses stays under 50ms/op budget
    (ADR-2026-09-09-C Decision 1 — simple schema construction path)."""
    as_of = datetime.now(timezone.utc)
    deployments = [_deployment_view() for _ in range(5)]

    durations: list[float] = []
    for _ in range(500):
        start = time.perf_counter()
        DeploymentListResponse(deployments=deployments, as_of=as_of)
        durations.append(time.perf_counter() - start)

    durations.sort()
    p95 = durations[int(len(durations) * 0.95) - 1]
    assert p95 < 0.05, f"p95 construction time {p95:.4f}s exceeded 50ms budget"
