"""Paper Execution & Control API contracts v1 — pydantic schema unit tests.

Spec: AIOSproject 47_paper_execution_and_control_center_specification_v1.0.md,
77_paper_execution_control_l3_build_and_operational_specification_v1.0.md §4,
107_contract_versioning_and_compatibility_standard_v1.0.md.
task-4645.
"""
from __future__ import annotations

import time
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.paper_control.contracts.v1 import (
    SCHEMA_VERSION,
    DeploymentCommandRequest,
    DeploymentState,
    PaperDeploymentView,
    RequestDeploymentRequest,
)


def _request_deployment_kwargs(**overrides):
    defaults = dict(
        package_ref="pkg-1",
        connection_id=uuid4(),
        adapter_type="fake-paper-v1",
        provider_sandbox_account_ref="sandbox-acct-1",
        idempotency_key="idem-1",
    )
    defaults.update(overrides)
    return defaults


def _view_kwargs(**overrides):
    defaults = dict(
        id=uuid4(),
        package_ref="pkg-1",
        connection_id=uuid4(),
        state=DeploymentState.REQUESTED,
        fence_token=1,
        created_at=None,
        updated_at=None,
    )
    defaults.update(overrides)
    return defaults


# --- positive construction ---------------------------------------------------


def test_request_deployment_request_holds_fields_as_given():
    req = RequestDeploymentRequest(**_request_deployment_kwargs())
    assert req.package_ref == "pkg-1"
    assert req.endpoint_classification == "SANDBOX"
    assert req.idempotency_key == "idem-1"


def test_request_deployment_request_allows_null_connection_id():
    req = RequestDeploymentRequest(**_request_deployment_kwargs(connection_id=None))
    assert req.connection_id is None


def test_deployment_command_request_holds_idempotency_key():
    cmd = DeploymentCommandRequest(idempotency_key="idem-2")
    assert cmd.idempotency_key == "idem-2"


def test_paper_deployment_view_holds_fields_and_default_schema_version():
    view = PaperDeploymentView(**_view_kwargs(state=DeploymentState.RUNNING, fence_token=3))
    assert view.state is DeploymentState.RUNNING
    assert view.fence_token == 3
    assert view.schema_version == SCHEMA_VERSION


# --- negative: invalid enum / missing / wrong-type input ---------------------


def test_deployment_state_rejects_value_outside_enum():
    with pytest.raises(ValueError):
        DeploymentState("NOT_A_STATE")


def test_request_deployment_request_rejects_missing_required_field():
    incomplete = _request_deployment_kwargs()
    del incomplete["idempotency_key"]
    with pytest.raises(ValidationError):
        RequestDeploymentRequest(**incomplete)


def test_deployment_command_request_rejects_missing_idempotency_key():
    with pytest.raises(ValidationError):
        DeploymentCommandRequest()


def test_paper_deployment_view_rejects_invalid_state_value():
    kwargs = _view_kwargs()
    kwargs["state"] = "NOT_A_STATE"
    with pytest.raises(ValidationError):
        PaperDeploymentView(**kwargs)


def test_paper_deployment_view_rejects_non_uuid_id():
    kwargs = _view_kwargs()
    kwargs["id"] = "not-a-uuid"
    with pytest.raises(ValidationError):
        PaperDeploymentView(**kwargs)


# --- failure injection: corrupted upstream data must fail closed -------------


def _row_to_view(row: dict) -> PaperDeploymentView:
    """Stand-in for a repository row -> API view mapper (47 boundary)."""
    return PaperDeploymentView(**row)


def test_view_mapping_fails_closed_on_corrupted_repository_row(monkeypatch):
    """Failure injection: simulate a dependency (repository row) handing back a
    fence_token outside its declared int type (e.g. manual DB edit or schema
    drift) and confirm the boundary fails closed instead of silently coercing
    it into a usable view."""
    row = _view_kwargs()
    monkeypatch.setitem(row, "fence_token", object())
    with pytest.raises(ValidationError):
        _row_to_view(row)


# --- performance assertion ----------------------------------------------------


def test_paper_deployment_view_construction_throughput():
    """Pydantic model construction has no I/O; 5k instances must build well
    under 1s (budget: >= 5k ops/sec) — regression guard against someone later
    adding hidden validation/I/O to this contract model."""
    iterations = 5_000
    kwargs = _view_kwargs()
    started = time.perf_counter()
    for _ in range(iterations):
        PaperDeploymentView(**kwargs)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"expected < 1.0s for {iterations} constructions, took {elapsed:.3f}s"
