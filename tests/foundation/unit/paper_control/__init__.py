"""FND-07 submission regressions: I-02 fencing and I-07/I-10 fail-closed wiring.

Explicitly collected by task-8405's pytest command. No external I/O is used.
"""
from dataclasses import replace
from importlib import import_module
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.foundation.paper_control.domain.models import (
    AdapterProvenance,
    CredentialClass,
    DeploymentState,
    PaperDeployment,
)
from src.foundation.risk_gate.contracts.v1 import RiskOutcome

_submission = import_module("src.foundation.paper_control.application.submit_paper_intent")


@pytest.fixture
def submission_case(monkeypatch):
    deployment = PaperDeployment(
        id=uuid4(), tenant_id=uuid4(), connection_id=None, package_ref="pkg-test",
        mandate_revision_id=uuid4(),
        provenance=AdapterProvenance("fake-paper-v1", CredentialClass.PAPER, "SANDBOX", "test"),
        state=DeploymentState.RUNNING, fence_token=7,
    )
    repo = SimpleNamespace(
        get_deployment=AsyncMock(return_value=deployment),
        transition_deployment_state=AsyncMock(),
        insert_order_intent=AsyncMock(side_effect=lambda intent: intent),
    )
    adapter = SimpleNamespace(
        submit_paper_intent=AsyncMock(
            return_value=SimpleNamespace(provider_order_ref="paper-order-test")
        ),
        cancel_paper_order=AsyncMock(),
    )
    gate = AsyncMock(return_value=SimpleNamespace(outcome=RiskOutcome.ALLOW, reason_codes=[]))
    monkeypatch.setattr(_submission, "evaluate_risk_gate", gate)
    return deployment, repo, adapter, gate


async def _submit(case, fence=7):
    deployment, repo, adapter, _ = case
    return await _submission.submit_paper_intent(
        repo, adapter, object(), object(), object(),
        deployment_id=deployment.id, expected_fence_token=fence, sequence=1,
    )


def _assert_no_submission(case):
    _, repo, adapter, _ = case
    adapter.submit_paper_intent.assert_not_awaited()
    adapter.cancel_paper_order.assert_not_awaited()
    repo.insert_order_intent.assert_not_awaited()
    repo.transition_deployment_state.assert_not_awaited()


async def test_negative_stale_fence_never_reaches_adapter(submission_case):
    with pytest.raises(_submission.FenceSupersededError):
        await _submit(submission_case, fence=6)
    _assert_no_submission(submission_case)
    submission_case[3].assert_not_awaited()


@pytest.mark.parametrize("state", [DeploymentState.PAUSED, DeploymentState.STOPPED,
                                    DeploymentState.RECOVERY_REVIEW])
async def test_negative_non_running_deployment_rejects_intent(submission_case, state):
    deployment, repo, _, gate = submission_case
    repo.get_deployment.return_value = replace(deployment, state=state)
    with pytest.raises(_submission.FenceSupersededError):
        await _submit(submission_case)
    _assert_no_submission(submission_case)
    gate.assert_not_awaited()


async def test_negative_risk_denial_blocks_adapter(submission_case):
    gate = submission_case[3]
    gate.return_value = SimpleNamespace(outcome=RiskOutcome.DENY, reason_codes=["KILL_SWITCH"])
    with pytest.raises(_submission.RiskGateDeniedError) as error:
        await _submit(submission_case)
    assert error.value.reason_codes == ["KILL_SWITCH"]
    gate.assert_awaited_once()
    _assert_no_submission(submission_case)


async def test_failure_injection_risk_dependency_timeout_is_fail_closed(submission_case):
    failure = TimeoutError("risk storage unavailable")
    submission_case[3].side_effect = failure
    with pytest.raises(TimeoutError) as error:
        await _submit(submission_case)
    assert error.value is failure
    _assert_no_submission(submission_case)


async def test_failure_injection_provider_timeout_degrades_without_intent(submission_case):
    deployment, repo, adapter, _ = submission_case
    failure = TimeoutError("provider unavailable")
    adapter.submit_paper_intent.side_effect = failure
    with pytest.raises(_submission.ProviderUnavailableError) as error:
        await _submit(submission_case)
    assert error.value.__cause__ is failure
    repo.transition_deployment_state.assert_awaited_once_with(
        deployment.id, expected_state="RUNNING", new_state="DEGRADED",
    )
    repo.insert_order_intent.assert_not_awaited()
    adapter.cancel_paper_order.assert_not_awaited()


async def test_adversarial_fence_change_during_ack_cancels_order(submission_case):
    deployment, repo, adapter, _ = submission_case
    repo.get_deployment.side_effect = [deployment, replace(deployment, fence_token=8)]
    with pytest.raises(_submission.FenceSupersededError):
        await _submit(submission_case)
    adapter.submit_paper_intent.assert_awaited_once()
    adapter.cancel_paper_order.assert_awaited_once()
    context, order_ref = adapter.cancel_paper_order.await_args.args
    assert context.deployment_id == str(deployment.id)
    assert order_ref == "paper-order-test"
    repo.insert_order_intent.assert_not_awaited()


@pytest.mark.perf
async def test_paper_submission_p95_under_50ms_with_in_memory_dependencies(submission_case):
    """ADR-2026-09-09-C: local orchestration budget; excludes provider/network latency."""
    samples = []
    for _ in range(100):
        started = perf_counter()
        intent = await _submit(submission_case)
        samples.append(perf_counter() - started)
        assert intent.state == "SUBMITTED"
        assert intent.fence_token_at_submit == 7
    assert sorted(samples)[94] < 0.050
    assert submission_case[2].submit_paper_intent.await_count == 100
