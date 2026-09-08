"""R-46 intraday monitor -- drawdown/stale/provider/recon -> risk_signal
-> PAUSE control (ACCOUNT/PROVIDER) when needed.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#§9 R-46, §2 table row 109,
§6 row 453 (dedupe), §7 "signal->PAUSE <=2s" SLO.

Design decisions the spec leaves open (this leaf's judgment calls):

- `repos.metrics` is an already-assembled `Sequence[AccountMetrics]`
  snapshot -- this module owns policy (threshold breach -> signal ->
  pause), not data collection (the same boundary `RiskEvaluationInput`
  already uses). Wiring the real assembly from equity_tracker/
  data_freshness/connection health/reconcile is a later leaf
  (`main.py`).
- Every emitted signal is CRITICAL (no WARN tier) -- this pass fires
  only when the threshold already supplied on `AccountMetrics` is
  actually breached, and a softer WARN tier is out of this leaf's DoD,
  so it is not guessed at.
- Only DRAWDOWN/STALE_DATA (scope=ACCOUNT) and PROVIDER_OUTAGE
  (scope=PROVIDER) trigger a PAUSE control (spec §9 line 559 "stale ->
  PAUSE ACCOUNT control"). RECON_MISMATCH is signal-only -- §6 line 473
  already routes that failure through a symbol-level DENY, and its
  stated recovery path is "reconcile confirmed", not a PAUSE.
- The PAUSE control's `actor_subject_id` is `metrics.tenant_id` --
  `KillSwitchService.activate`'s own docstring establishes "every
  caller uses tenant_id == actor_subject_id" as this codebase's
  convention, and there is no separate "system" user row to use
  instead (`safety_control.actor_subject_id` FKs `users(user_id)` NOT
  NULL, so a synthetic nil UUID would itself violate the FK).
- PROVIDER_OUTAGE's `dedupe_key` is built from `scope_ref` (=
  provider_code) alone, with no tenant_id component (§6 row 453) -- so
  when multiple tenants observe the same provider outage in the same
  5-minute window, only the first `insert_if_new` call wins and issues
  the pause; the rest see `False` and move on (no duplicate pauses for
  the same provider).
- A single account's pause failure is logged and does not stop the
  pass for the remaining accounts (the same best-effort judgment
  `KillSwitchService._fan_out` already applies) -- the `risk_signal`
  row is already committed even if the pause itself fails.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

from src.foundation.risk_gate.adapters.postgres_signal_repository import dedupe_key_for
from src.foundation.risk_gate.domain.models import (
    RiskSignal,
    RiskSignalSeverity,
    RiskSignalState,
    RiskSignalType,
    SafetyScope,
)
from src.foundation.risk_gate.ports.repository import RiskSignalRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountMetrics:
    """One account/tenant's already-collected snapshot for a single pass
    (see module docstring)."""

    tenant_id: UUID
    account_ref: str
    provider_code: str | None
    equity_value: Decimal | None
    equity_peak: Decimal | None
    drawdown_limit_pct: Decimal
    candle_age_seconds: Decimal | None
    staleness_limit_seconds: Decimal
    provider_available: bool
    recon_mismatch: bool
    evidence_ref: str | None = None


class KillSwitchLike(Protocol):
    """Structural match for `KillSwitchService.activate` -- lets this
    module accept a test double without importing the concrete class."""

    async def activate(
        self,
        *,
        scope: SafetyScope,
        scope_ref: str | None,
        reason: str,
        actor_subject_id: UUID,
        actor_is_admin: bool,
        trace_id: UUID,
    ) -> object: ...


@dataclass(frozen=True)
class IntradayMonitorRepos:
    metrics: Sequence[AccountMetrics]
    signals: RiskSignalRepository
    kill_switch: KillSwitchLike


def _drawdown_breached(m: AccountMetrics) -> bool:
    if m.equity_value is None or m.equity_peak is None or m.equity_peak <= 0:
        return False
    drawdown_pct = (m.equity_peak - m.equity_value) / m.equity_peak * Decimal(100)
    return drawdown_pct >= m.drawdown_limit_pct


def _stale_breached(m: AccountMetrics) -> bool:
    if m.candle_age_seconds is None:
        return False
    return m.candle_age_seconds >= m.staleness_limit_seconds


_CHECKS: tuple[tuple[RiskSignalType, Callable[[AccountMetrics], bool]], ...] = (
    (RiskSignalType.DRAWDOWN, _drawdown_breached),
    (RiskSignalType.STALE_DATA, _stale_breached),
    (RiskSignalType.PROVIDER_OUTAGE, lambda m: not m.provider_available),
    (RiskSignalType.RECON_MISMATCH, lambda m: m.recon_mismatch),
)

_PAUSE_SCOPE: dict[RiskSignalType, SafetyScope] = {
    RiskSignalType.DRAWDOWN: SafetyScope.ACCOUNT,
    RiskSignalType.STALE_DATA: SafetyScope.ACCOUNT,
    RiskSignalType.PROVIDER_OUTAGE: SafetyScope.PROVIDER,
}


def _scope_ref_for(signal_type: RiskSignalType, m: AccountMetrics) -> str | None:
    if signal_type is RiskSignalType.PROVIDER_OUTAGE:
        return m.provider_code
    return m.account_ref


async def run_intraday_monitor_once(
    repos: IntradayMonitorRepos, *, now: datetime
) -> list[RiskSignal]:
    if now.utcoffset() is None:
        raise ValueError("now must be tz-aware (UTC)")

    emitted: list[RiskSignal] = []
    for metrics in repos.metrics:
        for signal_type, check in _CHECKS:
            if not check(metrics):
                continue
            scope_ref = _scope_ref_for(signal_type, metrics)
            if not scope_ref:
                logger.warning(
                    "intraday_monitor: %s breached for tenant=%s but scope_ref is missing",
                    signal_type.value,
                    metrics.tenant_id,
                )
                continue
            dedupe_key = dedupe_key_for(signal_type.value, scope_ref, now)
            signal_id = uuid4()
            created = await repos.signals.insert_if_new(
                signal_id=signal_id,
                dedupe_key=dedupe_key,
                tenant_id=metrics.tenant_id,
                signal_type=signal_type.value,
                severity=RiskSignalSeverity.CRITICAL.value,
                as_of=now,
                source="intraday_monitor",
                evidence_ref=metrics.evidence_ref,
            )
            if not created:
                continue  # RSK-008 dedupe -- already recorded this 5-min window

            emitted.append(
                RiskSignal(
                    id=signal_id,
                    tenant_id=metrics.tenant_id,
                    type=signal_type,
                    severity=RiskSignalSeverity.CRITICAL,
                    dedupe_key=dedupe_key,
                    as_of=now,
                    source="intraday_monitor",
                    evidence_ref=metrics.evidence_ref,
                    state=RiskSignalState.OPEN,
                    safety_control_id=None,
                )
            )

            pause_scope = _PAUSE_SCOPE.get(signal_type)
            if pause_scope is None:
                continue
            try:
                await repos.kill_switch.activate(
                    scope=pause_scope,
                    scope_ref=scope_ref,
                    reason=f"intraday_monitor:{signal_type.value}",
                    actor_subject_id=metrics.tenant_id,
                    actor_is_admin=True,
                    trace_id=uuid4(),
                )
            except Exception:  # noqa: BLE001 -- signal is already committed (see module docstring)
                logger.exception(
                    "intraday_monitor: PAUSE activation failed for %s scope=%s scope_ref=%s",
                    signal_type.value,
                    pause_scope.value,
                    scope_ref,
                )

    return emitted


__all__ = ["AccountMetrics", "IntradayMonitorRepos", "KillSwitchLike", "run_intraday_monitor_once"]
