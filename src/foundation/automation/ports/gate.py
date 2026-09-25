"""Gate port — live-trading actions (order/hedge/kill) must pass this port to
execute (U-4 DoD, INVARIANTS.md I-09: "two independent authorities"). The
actual risk_gate/mandate evaluation wiring (DB adapter) happens in a
follow-up leaf — this leaf only defines the contract and the fail-closed
composition rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from src.foundation.automation.contracts.v1 import Action
from src.foundation.risk_gate.contracts.v1 import RiskOutcome

__all__ = ["ActionIntent", "AutomationGatePort", "GateDecision"]


@dataclass(frozen=True, slots=True)
class ActionIntent:
    tenant_id: UUID
    rule_id: UUID
    action: Action
    trace_id: UUID


@dataclass(frozen=True, slots=True)
class GateDecision:
    """I-09: the final ALLOW/DENY must pass **both** the RiskEngine
    composite point and the Compliance bundle evaluation, each leaving its
    own queryable evidence (decision id). If either outcome isn't ALLOW or
    either decision id is missing, the whole thing is DENY (fail-closed) —
    there is no construction path that bypasses both checks."""

    risk_outcome: RiskOutcome
    risk_decision_id: UUID | None
    compliance_outcome: RiskOutcome
    compliance_decision_id: UUID | None
    reason_codes: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return (
            self.risk_outcome == RiskOutcome.ALLOW
            and self.compliance_outcome == RiskOutcome.ALLOW
            and self.risk_decision_id is not None
            and self.compliance_decision_id is not None
        )


class AutomationGatePort(Protocol):
    async def check(self, intent: ActionIntent) -> GateDecision: ...
