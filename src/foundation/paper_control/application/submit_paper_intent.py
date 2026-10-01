"""SubmitPaperIntent command — the actual tick workflow (scheduler) is not
in this leaf (a new concept unrelated to the §1 FROZEN area of spec 71, but
not yet implemented itself — see the migration docstring). This function only
provides the checkpoint a future scheduler or manual caller uses to confirm
"is it still OK to submit one order intent under this fence right now."

Spec: AIOSproject spec 77 §3 "Every tick ... verifies current state/fence
immediately before intent and immediately before adapter call. Superseded
fence token means no-op/audit, never late order submission." — this function
implements that "immediate check" as one atomic UPDATE (a conditional
state/token check only, without incrementing).

Reflects a cross-session audit finding (agent-platform-12, 2026-09-02) —
start_deployment/resume_deployment check the risk_gate on entry, but if an
admin flips the kill switch *after* the deployment reaches RUNNING, the
fence_token itself does not change (activating the kill switch does not
automatically trigger pause/stop on this deployment), so re-checking the
fence alone cannot block this path. We close the gap by re-checking the
risk_gate on every submission via GateKind.PRE_INTENT — evaluate_risk_gate()
caches itself with a 10s TTL (spec 78 §2), so this doesn't force a full
recomputation on every tick.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from src.core.observability.metric_names import (
    FOUNDATION_PAPER_CONTROL_ORDER_INTENT_COUNT_TOTAL,
)
from src.core.observability.metrics import MetricsPort, NullMetrics
from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.paper_control.application.start_deployment import RiskGateDeniedError
from src.foundation.paper_control.domain.models import (
    CredentialClass,
    DeploymentState,
    PaperOrderIntent,
)
from src.foundation.paper_control.ports.paper_adapter import (
    PaperExecutionAdapter,
    PaperExecutionContext,
)
from src.foundation.paper_control.ports.repository import PaperControlRepository
from src.foundation.risk_gate.api import GateKind
from src.foundation.risk_gate.application.evaluate_risk_gate import evaluate_risk_gate
from src.foundation.risk_gate.contracts.v1 import RiskOutcome
from src.foundation.risk_gate.ports.repository import RiskGateRepository

__all__ = [
    "DeploymentNotFoundError",
    "FenceSupersededError",
    "ProviderUnavailableError",
    "RiskGateDeniedError",
    "submit_paper_intent",
]
"""Re-export RiskGateDeniedError here too — after a review found that
start_deployment.py and pause_deployment.py had each defined their own
independent InvalidDeploymentStateError (same name, different classes,
which almost let pytest.raises silently pass on the wrong one), we decided
not to repeat that mistake — reuse start_deployment.py's class instead of
defining a new one."""


class DeploymentNotFoundError(Exception):
    pass


class FenceSupersededError(Exception):
    """PAP-004 — submission under this fence is no longer possible (a
    pause/stop already advanced the fence in the meantime). This is a
    no-op, not a late order submission."""


class ProviderUnavailableError(Exception):
    """PAP-007 "provider timeout produces DEGRADED/retry policy and never
    switches modes" — if the paper adapter call fails (even a simulation
    can time out or be rejected), we drop RUNNING to DEGRADED and do not
    expose the raw adapter exception (same principle as the spec 72 §4
    error taxonomy). "Never switches modes" means this exception moves the
    state only, leaving mode=PAPER unchanged."""


async def submit_paper_intent(
    repo: PaperControlRepository,
    adapter: PaperExecutionAdapter,
    risk_repo: RiskGateRepository,
    mandate_repo: MandateRepository,
    connection_repo: ConnectionRepository,
    *,
    deployment_id: UUID,
    expected_fence_token: int,
    sequence: int,
    metrics: MetricsPort | None = None,
) -> PaperOrderIntent:
    metrics = metrics if metrics is not None else NullMetrics()
    deployment = await repo.get_deployment(deployment_id)
    if deployment is None:
        raise DeploymentNotFoundError(str(deployment_id))

    # Spec 77 §3 "verifies ... immediately before intent" — check this
    # before calling the adapter.
    fence_stale = deployment.fence_token != expected_fence_token
    if deployment.state != DeploymentState.RUNNING or fence_stale:
        raise FenceSupersededError(
            f"deployment.id={deployment_id}: fence {expected_fence_token}는 더 이상 "
            f"유효하지 않습니다(현재 상태={deployment.state.value}, "
            f"현재 fence={deployment.fence_token})."
        )

    # Reflects the cross-session audit finding — even if the fence is
    # valid, the kill switch may have been flipped on *while* RUNNING.
    # Re-check right before each submission via the PRE_INTENT gate.
    risk_result = await evaluate_risk_gate(
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=deployment.tenant_id,
        gate_kind=GateKind.PRE_INTENT,
        connection_id=deployment.connection_id,
    )
    if risk_result.outcome != RiskOutcome.ALLOW:
        raise RiskGateDeniedError(risk_result.reason_codes)

    context = PaperExecutionContext(
        deployment_id=str(deployment_id), provenance=deployment.provenance
    )
    try:
        ack = await adapter.submit_paper_intent(context, sequence)
    except Exception as exc:
        # PAP-007 — an adapter call failure is a provider problem, not a
        # fence problem. Another request may have already changed the
        # state away from RUNNING, so we only drop it conditionally — if
        # that fails too (already DEGRADED/PAUSED etc.), ignore it and
        # re-raise the original exception unchanged (spec 105 §2.2, so a
        # state-transition failure never becomes a secondary exception
        # that masks the "more urgent cause").
        try:
            await repo.transition_deployment_state(
                deployment_id,
                tenant_id=deployment.tenant_id,
                expected_state=DeploymentState.RUNNING.value,
                new_state=DeploymentState.DEGRADED.value,
            )
        except Exception:  # noqa: BLE001 — per the comment above, don't mask the original exception
            pass
        raise ProviderUnavailableError("DEPENDENCY_PAPER_PROVIDER_UNAVAILABLE") from exc

    # Re-check for "immediately before adapter call" — the adapter call
    # itself is a network round trip, so a pause/stop could have committed
    # in the meantime.
    fresh = await repo.get_deployment(deployment_id)
    if (
        fresh is None
        or fresh.state != DeploymentState.RUNNING
        or fresh.fence_token != expected_fence_token
    ):
        await adapter.cancel_paper_order(context, ack.provider_order_ref)
        raise FenceSupersededError(
            f"deployment.id={deployment_id}: adapter 응답 도착 전에 fence가 superseded됐습니다 "
            "— 즉시 취소했습니다."
        )

    # PLT-10 §7.2 `aios.foundation_paper_control.order_intent.count_total` —
    # the A4 alert (§7.4) treats a mode="live_blocked" occurrence itself as
    # critical, so we structurally distinguish the non-PAPER credential_class
    # case here (today CredentialClass only has PAPER, so this is always
    # "paper").
    is_paper = deployment.provenance.credential_class == CredentialClass.PAPER
    mode = "paper" if is_paper else "live_blocked"
    metrics.counter(FOUNDATION_PAPER_CONTROL_ORDER_INTENT_COUNT_TOTAL, labels={"mode": mode})

    return await repo.insert_order_intent(
        PaperOrderIntent(
            id=uuid4(),
            deployment_id=deployment_id,
            sequence=sequence,
            fence_token_at_submit=expected_fence_token,
            state="SUBMITTED",
        )
    )
