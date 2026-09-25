"""task-6510 (XREV task-3820 REJECT, finding id=149) -- the always-on
periodic caller: `personal_mode_account_id()` -> real NAV state
(`personal_risk_snapshot_source.py`) -> `check_personal_daily_loss`
(`monitor_daily_loss.py`). `run_personal_daily_loss_monitor_tick` is the
pure application tick; `src/services/background_loops.py` is the
composition root that wires it into a real `asyncio.Task` on
`run_periodic_loop` with the concrete adapters injected (RATCHET-2
`foundation-aggregates` -- this module may not import
`positions.adapters.postgres_nav_repository`, and importing
`services.background_loops` here would cycle back into this module) so
the 3% daily-loss auto-kill fires even with no order in flight (I-10: a
safety component must be wired, not merely implementable).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import asyncpg

from src.foundation.positions.ports.nav_repository import NavRepository
from src.foundation.risk.adapters.bundle_loader import load_personal_bundle
from src.foundation.risk.application.monitor_daily_loss import check_personal_daily_loss
from src.foundation.risk.application.personal_risk_snapshot_source import (
    compute_personal_daily_loss_input,
)
from src.foundation.risk.ports.notifier import PersonalNotifierPort
from src.foundation.risk.ports.state import PersonalOperationStatePort

logger = logging.getLogger(__name__)

# task-6510 -- personal-conservative daily-loss auto-kill's always-on caller (XREV task-3820
# REJECT fix). Same order of magnitude as background_loops.py's RISK_GUARD_INTERVAL_SECONDS
# (also a loss-limit watch).
PERSONAL_DAILY_LOSS_MONITOR_INTERVAL_SECONDS = 30.0


async def run_personal_daily_loss_monitor_tick(
    pool: asyncpg.Pool,
    *,
    personal_state: PersonalOperationStatePort,
    personal_notifier: PersonalNotifierPort,
    nav_repo: NavRepository,
    now: datetime | None = None,
) -> bool:
    """Returns whether kill is (now or already) engaged -- mostly useful
    for tests. A true no-op (returns `False`, touches nothing) whenever
    personal mode is not scoped to any account -- same per-account opt-in
    semantics as `foundation_personal_gate.evaluate_personal_layer`."""
    scoped_account_id = await personal_state.personal_mode_account_id()
    if scoped_account_id is None:
        return False

    moment = now if now is not None else datetime.now(timezone.utc)
    daily_input = await compute_personal_daily_loss_input(
        pool, scoped_account_id, moment.date(), nav_repo=nav_repo
    )
    if daily_input is None:
        return False

    try:
        bundle = load_personal_bundle()
    except Exception:
        # Fail-safe, not fail-closed here: unlike foundation_personal_gate.py's
        # per-order path (which denies the one order in front of it on a
        # broken bundle), this tick has no order to deny -- a missing/broken
        # config file just means the threshold itself is unknown, so this
        # tick has nothing to check against and skips (next tick retries).
        logger.exception(
            "personal_daily_loss_monitor: personal-conservative 번들 로드 실패 "
            "-- 이번 주기 건너뜁니다."
        )
        return False

    return await check_personal_daily_loss(
        bundle,
        account_equity_krw=daily_input.account_equity_krw,
        daily_realized_pnl_pct=daily_input.daily_realized_pnl_pct,
        notifier=personal_notifier,
        state=personal_state,
    )
