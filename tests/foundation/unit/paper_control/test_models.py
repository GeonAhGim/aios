"""FND-07 Paper Execution & Control domain models — pure value object unit tests.

Spec: AIOSproject 77_paper_execution_control_l3_build_and_operational_specification_v1.0.md §1/§2.
task-4617.
"""
from __future__ import annotations

import dataclasses
import time
from typing import Any
from uuid import uuid4

import pytest

from src.foundation.paper_control.domain.models import (
    AdapterProvenance,
    CommandOutcome,
    CommandType,
    CredentialClass,
    DeploymentCommand,
    DeploymentState,
    PaperDeployment,
    PaperOrderIntent,
)


def _provenance(**overrides: Any) -> AdapterProvenance:
    defaults: dict[str, Any] = dict(
        adapter_type="fake-paper-v1",
        credential_class=CredentialClass.PAPER,
        endpoint_classification="SANDBOX",
        provider_sandbox_account_ref="sandbox-acct-1",
    )
    defaults.update(overrides)
    return AdapterProvenance(**defaults)


def _deployment(**overrides: Any) -> PaperDeployment:
    defaults: dict[str, Any] = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        connection_id=None,
        package_ref="pkg-1",
        mandate_revision_id=uuid4(),
        provenance=_provenance(),
        state=DeploymentState.REQUESTED,
        fence_token=1,
    )
    defaults.update(overrides)
    return PaperDeployment(**defaults)


# --- positive construction ---------------------------------------------------


def test_paper_deployment_holds_fields_as_given():
    deployment_id = uuid4()
    deployment = _deployment(id=deployment_id, state=DeploymentState.RUNNING, fence_token=7)
    assert deployment.id == deployment_id
    assert deployment.state is DeploymentState.RUNNING
    assert deployment.fence_token == 7
    assert deployment.request_idempotency_key is None
    assert deployment.created_at is None


def test_deployment_command_holds_fields_as_given():
    command = DeploymentCommand(
        id=uuid4(),
        deployment_id=uuid4(),
        idempotency_key="idem-1",
        command_type=CommandType.START,
        actor_subject_id=uuid4(),
        outcome=CommandOutcome.ACCEPTED,
        detail=None,
    )
    assert command.command_type is CommandType.START
    assert command.outcome is CommandOutcome.ACCEPTED


def test_paper_order_intent_holds_fields_as_given():
    intent = PaperOrderIntent(
        id=uuid4(),
        deployment_id=uuid4(),
        sequence=1,
        fence_token_at_submit=1,
        state="SUBMITTED",
    )
    assert intent.sequence == 1
    assert intent.state == "SUBMITTED"


# --- negative: invalid enum construction (boundary/invalid input) ------------


def test_deployment_state_rejects_value_outside_enum():
    with pytest.raises(ValueError):
        DeploymentState("NOT_A_STATE")


def test_credential_class_rejects_live_value():
    """77 §1 — this context only ever handles PAPER; LIVE is not pre-created here."""
    with pytest.raises(ValueError):
        CredentialClass("LIVE")


def test_command_outcome_rejects_value_outside_enum():
    with pytest.raises(ValueError):
        CommandOutcome("PENDING")


def test_command_type_rejects_value_outside_enum():
    with pytest.raises(ValueError):
        CommandType("CANCEL")


# --- negative: frozen dataclass immutability ----------------------------------


def test_paper_deployment_is_immutable():
    deployment = _deployment()
    field = "state"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(deployment, field, DeploymentState.RUNNING)


def test_adapter_provenance_is_immutable():
    provenance = _provenance()
    field = "adapter_type"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(provenance, field, "other")


def test_deployment_command_is_immutable():
    command = DeploymentCommand(
        id=uuid4(),
        deployment_id=uuid4(),
        idempotency_key="idem-1",
        command_type=CommandType.START,
        actor_subject_id=uuid4(),
        outcome=CommandOutcome.ACCEPTED,
        detail=None,
    )
    field = "outcome"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(command, field, CommandOutcome.DENIED)


# --- negative: required field missing -----------------------------------------


def test_paper_deployment_requires_provenance():
    incomplete: dict[str, Any] = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        connection_id=None,
        package_ref="pkg-1",
        mandate_revision_id=uuid4(),
        state=DeploymentState.REQUESTED,
        fence_token=1,
    )
    with pytest.raises(TypeError):
        PaperDeployment(**incomplete)


# --- failure injection: corrupted upstream data must fail closed -------------


def _row_to_state(row: dict[str, str]) -> DeploymentState:
    """Stand-in for a repository row -> domain mapper (77 §1 boundary)."""
    return DeploymentState(row["state"])


def test_state_mapping_fails_closed_on_corrupted_repository_row(monkeypatch):
    """Failure injection: simulate a dependency (repository row) handing back a
    value outside the domain's enum — e.g. manual DB edit or schema drift — and
    confirm the boundary fails closed instead of silently coercing it."""
    row = {"state": DeploymentState.RUNNING.value}
    monkeypatch.setitem(row, "state", "DRIFTED_STATE")
    with pytest.raises(ValueError):
        _row_to_state(row)


# --- performance assertion ----------------------------------------------------


@pytest.mark.perf
def test_paper_deployment_construction_throughput():
    """Pure value-object construction has no I/O; 20k instances must build in
    well under 1s (budget: >= 20k ops/sec) — regression guard against someone
    later adding hidden validation/I/O to this frozen dataclass."""
    iterations = 20_000
    started = time.perf_counter()
    for _ in range(iterations):
        _deployment()
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"expected < 1.0s for {iterations} constructions, took {elapsed:.3f}s"
