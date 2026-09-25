"""L4_product_experience_and_discovery_v1.0.md §2.4 UX-14 --
`application/mirror_signal.py`: source signal -> follower order intent
(must pass the risk and compliance gates).

UX-A3 ("a follow order also passes the follower's own risk/compliance gate --
no inheriting the source account's authority") -- this function always calls
`evaluate_risk_gate`/`evaluate_pre_trade` with
`subscription.follower_tenant_id`/`subscription.follower_portfolio` only. The
source account's tenant_id (the owner of the strategy behind `source_listing`)
never appears in this function's signature -- there is no authority to
inherit because the caller never holds it.

If either gate is not ALLOW, this raises `MirrorGateDeniedError` and fails
closed. Unlike `order_service`'s `foundation_compliance.py`, this leaf does
not offer a passthrough flag for a missing mandate -- a follow subscription
must always clear both gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.follow.contracts.v1 import FollowSubscription, SourceSignal
from src.foundation.follow.domain.mirror_rules import MirrorOrderDraft, convert_signal
from src.foundation.mandates.application.evaluate_pre_trade import (
    ComplianceBundleInactiveError,
    ComplianceMandateMissingError,
    evaluate_pre_trade,
)
from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.risk_gate.api import GateKind
from src.foundation.risk_gate.application.evaluate_risk_gate import evaluate_risk_gate
from src.foundation.risk_gate.contracts.v1 import RiskOutcome
from src.foundation.risk_gate.ports.repository import RiskGateRepository

__all__ = [
    "MirrorGateDeniedError",
    "MirrorOrderIntent",
    "mirror_signal",
]


class MirrorGateDeniedError(Exception):
    """The follower's own risk or compliance gate returned DENY -- never
    bypassed via the source account's authority (UX-A3), always fail-closed."""

    def __init__(
        self, *, gate: Literal["risk", "compliance"], reason_codes: tuple[str, ...]
    ) -> None:
        self.gate = gate
        self.reason_codes = reason_codes
        super().__init__(f"FOLLOW_{gate.upper()}_GATE_DENIED: {reason_codes}")


@dataclass(frozen=True)
class MirrorOrderIntent:
    """The follower order intent produced once both gates clear -- not yet a
    real order or event (spec §2.4 "order intent"). `paper_only` is pinned to
    the literal `True` rather than copied from `subscription.paper_only`,
    because this type itself must prove that a LIVE intent cannot be
    constructed at all (UX-A3 paper_only invariant)."""

    subscription_id: UUID
    draft: MirrorOrderDraft
    risk_reason_codes: tuple[str, ...]
    compliance_decision_id: UUID
    paper_only: Literal[True] = True


async def mirror_signal(
    risk_repo: RiskGateRepository,
    mandate_repo: MandateRepository,
    connection_repo: ConnectionRepository,
    *,
    subscription: FollowSubscription,
    signal: SourceSignal,
    now: datetime,
) -> MirrorOrderIntent:
    """spec §2.4 UX-14. Order: domain rejection (free) -> risk gate ->
    compliance gate -> intent. Any stage denying stops the pipeline there, so
    the rejection reason always points at the actual first failure and no
    gate is called unnecessarily."""
    draft = convert_signal(subscription, signal)

    risk_result = await evaluate_risk_gate(
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=subscription.follower_tenant_id,
        gate_kind=GateKind.PRE_INTENT,
        connection_id=None,
    )
    if risk_result.outcome != RiskOutcome.ALLOW:
        raise MirrorGateDeniedError(gate="risk", reason_codes=tuple(risk_result.reason_codes))

    try:
        compliance_result = await evaluate_pre_trade(
            mandate_repo,
            tenant_id=subscription.follower_tenant_id,
            portfolio_id=subscription.follower_portfolio,
            snapshot={"symbol": draft.symbol},
            now=now,
        )
    except ComplianceMandateMissingError as exc:
        raise MirrorGateDeniedError(
            gate="compliance", reason_codes=("CM_MANDATE_MISSING",)
        ) from exc
    except ComplianceBundleInactiveError as exc:
        raise MirrorGateDeniedError(
            gate="compliance", reason_codes=("CM_BUNDLE_INACTIVE",)
        ) from exc

    if compliance_result.verdict != ComplianceVerdict.ALLOW:
        raise MirrorGateDeniedError(
            gate="compliance", reason_codes=tuple(compliance_result.reason_codes)
        )

    return MirrorOrderIntent(
        subscription_id=subscription.id,
        draft=draft,
        risk_reason_codes=tuple(risk_result.reason_codes),
        compliance_decision_id=compliance_result.compliance_decision_id,
    )
