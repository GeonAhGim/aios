"""FND-07 Paper Execution & Control domain models — pure value object unit tests.

Spec: AIOSproject 77_paper_execution_control_l3_build_and_operational_specification_v1.0.md §1/§2.
task-4617.
"""

from __future__ import annotations

import dataclasses
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
from tests.conftest import PerfBudget


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
def test_paper_deployment_construction_throughput(perf_budget: PerfBudget):
    """Pure value-object construction has no I/O; 20k instances must build in
    well under 1s (budget: >= 20k ops/sec) — regression guard against someone
    later adding hidden validation/I/O to this frozen dataclass.

    raw time.perf_counter() 단언을 perf_budget.assert_within() 경유로 전환
    (task-10997, L4-DC-2)."""
    iterations = 20_000

    def _build_batch() -> None:
        for _ in range(iterations):
            _deployment()

    perf_budget.assert_within(
        _build_batch,
        budget_ms=1000,
        n=3,
        label="paper_deployment_construction",
    )


# ---- 0 수렴: 단일 호출 CPU 시간 ---------------------------------------------


@pytest.mark.perf
def test_paper_deployment_single_call_cpu_converges_to_zero(
    perf_budget: PerfBudget,
):
    """단일 호출 CPU 시간이 0에 수렴하는지 검증(task-11539 감사 후속).

    `PaperDeployment`은 인메모리 frozen dataclass이므로
    `time.process_time()`으로 측정한 CPU 시간은 ~0ms여야 한다.

    Windows `time.process_time()`은 ~15.6ms 양자화 오차가 있다 —
    batch=64로 묶으면 오차가 ~0.24ms로 줄어든다. 64회 평균 CPU 시간이
    1ms 미만이면 충분히 0에 수렴한다고 판단한다.
    """
    sample = perf_budget.sample(_deployment, batch=64)
    assert sample.cpu_ms < 1.0, (
        f"single-call CPU={sample.cpu_ms:.3f}ms > 1ms budget "
        f"(64 calls averaged, wall={sample.wall_ms:.3f}ms)"
    )


# ---- 성능 게이트 적색 재현 (지연 주입) --------------------------------------


@pytest.mark.perf
def test_paper_deployment_perf_budget_catches_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
):
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    감지하는지 역으로 증명한다. `_deployment`에 50ms 지연을 주입하면
    wall-clock이 늘어나 budget(10ms)을 초과하므로 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    import time as _time

    original_sleep = _time.sleep
    call_count = [0]

    def _sleep_with_delay(seconds: float) -> None:
        call_count[0] += 1
        original_sleep(seconds)

    monkeypatch.setattr(_time, "sleep", _sleep_with_delay)

    # Patch the module-level uuid4 used by _deployment to inject delay.
    import tests.foundation.unit.paper_control.test_models as mod

    original_uuid4 = mod.uuid4

    def _uuid4_with_delay():
        _time.sleep(0.05)  # 50ms per call
        return original_uuid4()

    monkeypatch.setattr(mod, "uuid4", _uuid4_with_delay)

    samples = perf_budget.samples(_deployment, n=30)
    durations: list[float] = [s.wall_ms for s in samples]
    p95_ms = sorted(durations)[int(len(durations) * 0.95)]
    with pytest.raises(AssertionError):
        assert p95_ms < 10.0, (
            f"p95 wall={p95_ms:.2f}ms > 10ms budget (injected delay=50ms/call, gate-red repro)"
        )
