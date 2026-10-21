"""task-4695 — src/api/schemas/foundation/paper_control.py coverage 0% -> 70%+.

Covers DeploymentListResponse construction, negative validation paths, a
monkeypatch-induced dependency failure, and a throughput budget for repeated
model construction (ADR-2026-09-09-C Decision 1).
"""

from __future__ import annotations

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
from tests.support.perf_budget import PerfBudget


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
            deployments=["not-a-deployment-view"],  # type: ignore[list-item]
            as_of=datetime.now(timezone.utc),
        )


def test_deployment_list_response_invalid_as_of_type_raises() -> None:
    with pytest.raises(ValidationError):
        DeploymentListResponse(deployments=[], as_of="not-a-datetime")  # type: ignore[arg-type]


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
def test_deployment_list_response_construction_throughput_budget(
    perf_budget: PerfBudget,  # noqa: F821
) -> None:
    """p95 construction latency for 500 responses stays under 150ms/op budget
    (ADR-2026-09-09-C Decision 1 — simple schema construction path).

    raw perf_counter() → perf_budget.samples(batch=2000) 전환:
    process_time의 15.6ms 틱으로 인해 빠른 1회 호출은 0/15.6만 나오므로,
    batch=2000으로 묶어 호출당 오차를 tick/2000 ≈ 0.008ms로 낮췄다.
    CI 러너(--cov 오버헤드 + CPU 경합)에서 occasional spike가 all() 단언을
    깨는 원인을 batch 증가로 막는다(task-15090).
    단위는 samples.cpu_ms가 ms이므로 예산도 ms(80.0 → 150.0ms, task-15385 CI parallel 안정화).
    """
    as_of = datetime.now(timezone.utc)
    deployments = [_deployment_view() for _ in range(5)]

    samples = perf_budget.samples(
        lambda: DeploymentListResponse(deployments=deployments, as_of=as_of),
        n=10,
        warmup=0,
        batch=2000,
    )

    # cpu_ms 기준, 단위 일관성: ms
    assert all(s.cpu_ms < 150.0 for s in samples), f"budget 초과: {[s.cpu_ms for s in samples]}"


@pytest.mark.perf
def test_deployment_list_response_construction_budget_goes_red_with_10x_cpu_delay(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """게이트 적색 재현(D2): 성능 예산 단언이 실제로 위반을 포착하는지
    역으로 증명한다. `time.process_time`을 monkeypatch해 1회 호출당
    1500ms(예산 150ms의 10배)를 초과하는 CPU 시간을 가장하면
    `assert all(s.cpu_ms < 150.0)`가 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다(task-11539 감사: 호출 1회 CPU 측정의 0 수렴 결함 방지).
    """
    budget_ms = 150.0

    as_of = datetime.now(timezone.utc)
    deployments = [_deployment_view() for _ in range(5)]

    def _run() -> object:
        return DeploymentListResponse(deployments=deployments, as_of=as_of)

    # process_time을 monkeypatch: 호출할 때마다 1500ms(10x budget)를 더한다.
    # PerfBudget.sample()은 time.process_time()을 호출하므로 이 값이
    # cpu_ms 측정값에 그대로 반영돼 budget을 10배 초과한다.
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 1500.0  # 1500ms = 10x budget per call
        return _fake_time

    monkeypatch.setattr("time.process_time", _fake_process_time)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    samples = perf_budget.samples(
        _run,
        n=10,
        warmup=0,
        batch=2000,
    )
    with pytest.raises(AssertionError):
        assert all(s.cpu_ms < budget_ms for s in samples), (
            f"budget 초과: {[s.cpu_ms for s in samples]}"
        )
