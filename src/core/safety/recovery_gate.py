"""L4_risk_and_safety_v1.0.md#4.3 (CB state transition table), §9 R-44 — pure reactivation rule.

Policy doc 8.6-B · invariant I5: HALTED/EMERGENCY cannot auto-downgrade;
reactivation requires all of:
`evidence_ref present ∧ metrics history below warning throughout cooldown
∧ RECOVERY decision ALLOW ∗ approval within TTL` (§4.3 CB table row 4).

This module is pure rules — it calls no I/O, DB, or clock directly.
Judgments that require a clock (e.g. approval TTL expiry) are pre-resolved
into the `approval_status` string injected by the caller
(`ApprovalRequest.status` pre-computed by the caller with `now`, e.g.
"APPROVED"/"EXPIRED") — this module never consults the clock itself.

`metrics_history` must judge "below warning" without receiving the policy
thresholds, so the only criterion that is always safe under any policy
setting is used — "the entire history is exactly at the baseline value (0)":
all CB indicators have 0 as their upper bound, and since all thresholds are
greater than 0, a value of 0 is always below every policy's warning level.
The caller (R-45 wiring) contracts that the history length maps 1:1 to
`cooldown_sec` — one sample per second appended in order.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, model_validator

from src.core.loader.risk_policy_loader import CircuitBreakerPolicy
from src.core.risk.decision import RiskOutcome
from src.core.safety.circuit_breaker import (
    CircuitBreakerLevel,
    CircuitBreakerMetrics,
    compute_level,
)

_REACTIVATABLE = (CircuitBreakerLevel.HALTED, CircuitBreakerLevel.EMERGENCY)


class RecoveryDecision(BaseModel, frozen=True):
    outcome: RiskOutcome
    reason_code: str | None = None

    @model_validator(mode="after")
    def _reason_matches_outcome(self) -> RecoveryDecision:
        # Same fail-closed contract as I2 — DENY cannot exist without a reason.
        if self.outcome == RiskOutcome.DENY and self.reason_code is None:
            raise ValueError("DENY는 reason_code가 필요하다")
        if self.outcome == RiskOutcome.ALLOW and self.reason_code is not None:
            raise ValueError("ALLOW는 reason_code를 가질 수 없다")
        return self


def _deny(reason_code: str) -> RecoveryDecision:
    return RecoveryDecision(outcome=RiskOutcome.DENY, reason_code=reason_code)


def _is_baseline(metrics: CircuitBreakerMetrics, policy: CircuitBreakerPolicy) -> bool:
    """§4.3 CB table row 4 -- a history sample is baseline when it stays
    *below the warning thresholds* (``compute_level`` yields NORMAL), not when
    every metric is exactly zero. ``data_delay_sec`` and ``api_disconnect_sec``
    are time-since-last-observation measurements, so a real tick never sees
    an exact zero; requiring zero made reactivation unreachable with live
    trackers. ``data_delay_sec=None`` ("unknown", R-43) is never baseline --
    ``compute_level`` already treats unknown delay as HALTED (fail-closed), the
    explicit check keeps that contract visible here.
    """
    if metrics.data_delay_sec is None:
        return False
    return compute_level(metrics, policy) == CircuitBreakerLevel.NORMAL


def can_reactivate(
    *,
    current_level: CircuitBreakerLevel,
    metrics_history: Sequence[CircuitBreakerMetrics],
    cooldown_sec: int,
    evidence_ref: str | None,
    approval_status: str,
    fresh_risk_outcome: RiskOutcome,
    policy: CircuitBreakerPolicy,
) -> RecoveryDecision:
    """§4.3 CB table row 4 — ALLOW only when all 4 conditions are met, DENY otherwise.

    ``policy`` supplies the thresholds that define a baseline sample (row 4:
    "metrics history below warning for the whole cooldown").
    """
    if current_level not in _REACTIVATABLE:
        return _deny("RECOVERY_LEVEL_NOT_DEGRADED")
    if not evidence_ref:
        return _deny("RECOVERY_EVIDENCE_MISSING")
    if cooldown_sec <= 0:
        return _deny("RECOVERY_COOLDOWN_NOT_MET")
    if len(metrics_history) < cooldown_sec:
        return _deny("RECOVERY_COOLDOWN_NOT_MET")
    if not all(_is_baseline(m, policy) for m in metrics_history):
        return _deny("RECOVERY_COOLDOWN_NOT_MET")
    if approval_status != "APPROVED":
        return _deny("RECOVERY_APPROVAL_NOT_APPROVED")
    if fresh_risk_outcome != RiskOutcome.ALLOW:
        return _deny("RECOVERY_FRESH_RISK_DENY")
    return RecoveryDecision(outcome=RiskOutcome.ALLOW)
