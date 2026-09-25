"""risk_gate aggregate's cross-aggregate import surface.

Other foundation aggregates that need risk_gate's domain enums by identity
(not just structural/string compatibility with `contracts.v1`'s versions --
e.g. constructing or comparing against risk_gate's own domain dataclasses)
import them from here instead of reaching into `risk_gate.domain.*`
directly (`boundary:foundation-aggregates`, task-5418). Pure re-export, no
behavior change: this is the same class object as `domain.models.GateKind`/
`SafetyScope`, not a converted copy.
"""
from __future__ import annotations

from src.foundation.risk_gate.domain.models import GateKind, SafetyScope

__all__ = ["GateKind", "SafetyScope"]
