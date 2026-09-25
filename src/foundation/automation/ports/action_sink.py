"""Live-trading action execution port — the actual order submission/hedge/
kill-switch wiring (execution_loop, KillSwitchService, ...) is done in a
follow-up leaf. This leaf only defines the contract; tests prove "gate DENY ->
zero sink calls" with a fake sink.

I-01: no method takes `gate_decision` as Optional/defaulted — the caller
(`application/execute_action.py`) only invokes these once the gate has
allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.foundation.automation.ports.gate import ActionIntent, GateDecision

__all__ = ["ActionResult", "AutomationActionSink"]


@dataclass(frozen=True, slots=True)
class ActionResult:
    executed: bool
    detail: str
    error: str | None = None


class AutomationActionSink(Protocol):
    async def submit_order(
        self, intent: ActionIntent, gate_decision: GateDecision
    ) -> ActionResult: ...

    async def submit_hedge(
        self, intent: ActionIntent, gate_decision: GateDecision
    ) -> ActionResult: ...

    async def trigger_kill(
        self, intent: ActionIntent, gate_decision: GateDecision
    ) -> ActionResult: ...
