"""Paper Execution & Control pure rule functions — must be testable in isolation without DB/HTTP.

Spec: AIOSproject 77_paper_execution_control_l3_build_and_operational_specification_v1.0.md §1/§2.
"""
from __future__ import annotations

from src.foundation.paper_control.domain.models import (
    AdapterProvenance,
    CredentialClass,
    DeploymentState,
)

# 77 §2 state transition table. REQUESTED->READY: this leaf merges REQUEST+PREPARE
# into a single command (request_deployment) executed immediately — there is no
# real async wait between the two commands (unlike ConfirmConnection in connections),
# so splitting them serves no purpose. No intermediate PREPARING state is created
# (explicit scope reduction; see migration docstring).
_ALLOWED_TRANSITIONS: dict[DeploymentState, frozenset[DeploymentState]] = {
    DeploymentState.REQUESTED: frozenset({DeploymentState.READY, DeploymentState.FAILED}),
    DeploymentState.READY: frozenset({DeploymentState.RUNNING, DeploymentState.STOPPED}),
    DeploymentState.RUNNING: frozenset(
        {DeploymentState.PAUSED, DeploymentState.STOPPED, DeploymentState.DEGRADED}
    ),
    DeploymentState.PAUSED: frozenset(
        {DeploymentState.RUNNING, DeploymentState.STOPPED, DeploymentState.DEGRADED}
    ),
    DeploymentState.DEGRADED: frozenset(
        {DeploymentState.STOPPED, DeploymentState.RECOVERY_REVIEW}
    ),
    DeploymentState.RECOVERY_REVIEW: frozenset({DeploymentState.STOPPED}),
    DeploymentState.STOPPED: frozenset(),
    DeploymentState.FAILED: frozenset(),
}
"""The absence of RECOVERY_REVIEW -> RUNNING in the table itself implements
77 §2 "RECOVERY_REVIEW cannot transition to RUNNING automatically" —
resume_deployment() is callable only from PAUSED, and the sole path out of
RECOVERY_REVIEW is STOP (forcing the user to create a completely new
deployment via a fresh REQUEST)."""


class InvalidDeploymentTransitionError(Exception):
    pass


class InvalidProvenanceError(Exception):
    """77 §1 "A boolean is_paper alone is insufficient" / PAP-002 —
    rejects the adapter before calling it when credential_class is not PAPER
    or required fields are empty."""


def is_transition_allowed(current: DeploymentState, target: DeploymentState) -> bool:
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


def require_transition_allowed(current: DeploymentState, target: DeploymentState) -> None:
    if not is_transition_allowed(current, target):
        raise InvalidDeploymentTransitionError(
            f"{current.value} -> {target.value} 전이는 허용되지 않습니다."
        )


def validate_provenance(provenance: AdapterProvenance) -> None:
    """PAP-002 "live endpoint, live credential, unknown adapter, or missing
    egress proof rejects before adapter call" — on this leaf, "egress proof"
    is replaced by `provider_sandbox_account_ref` (no real egress policy service exists yet;
    outside 71 §4 Contract ownership boundary)."""
    if provenance.credential_class != CredentialClass.PAPER:
        raise InvalidProvenanceError(
            f"credential_class={provenance.credential_class.value}는 PAPER가 아닙니다."
        )
    if not provenance.adapter_type.strip():
        raise InvalidProvenanceError("adapter_type이 비어있습니다.")
    if not provenance.provider_sandbox_account_ref.strip():
        raise InvalidProvenanceError("provider_sandbox_account_ref가 비어있습니다.")
    if "live" in provenance.endpoint_classification.lower():
        raise InvalidProvenanceError(
            f"endpoint_classification={provenance.endpoint_classification}는 "
            "LIVE 엔드포인트로 의심됩니다."
        )
