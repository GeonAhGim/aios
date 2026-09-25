"""L4_compliance_and_regulatory_v1.0.md#9 CM-12 -- notifies on a post-trade compliance block.

Spec: §6 failure-mode table -- a post-trade violation must both notify and block
the fund's new orders (operator releases it later). CM-11's
`run_daily_post_trade_batch` already does the block half, via
`KillSwitchService.activate`; this leaf adds the notify half -- until now a
violation activated a TENANT kill switch with no signal reaching an operator
(audit finding: no violation notification).

No new notification infrastructure: this reuses the existing
`execution.safety_block.applied` event/channel that `risk_guard_service.py`
already publishes for the same shape of event (a `KillSwitchService.activate`
side effect) -- `src/core/notifications/channel_policy.py` already routes it
to IN_APP, forced. Adding a second compliance-specific event type would only
fragment the one channel policy this event already has.

Best-effort like `KillSwitchService._fan_out`'s own notification step: the
`safety_control` row is already committed by the time this runs, so a publish
failure must not be raised back into the caller (that would make a batch tick
fail and retry work that already succeeded) -- it is logged instead.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import date
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]

_EVENT_TYPE = "execution.safety_block.applied"


async def notify_violations(
    publish: PublishFn | None,
    *,
    tenant_id: UUID,
    business_date: date,
    rule_codes: Sequence[str],
) -> None:
    """One event per newly-activated rule (callers must pass only the rule
    codes that just transitioned to ACTIVE, not every blocking rule found
    that day -- otherwise a rerun of an idempotent batch would re-notify for
    a block an operator has already seen)."""
    if publish is None:
        return
    for rule_code in rule_codes:
        payload = {
            "user_id": str(tenant_id),
            "reason": f"COMPLIANCE:{rule_code}:{business_date.isoformat()}",
            "rule_code": rule_code,
            "business_date": business_date.isoformat(),
        }
        try:
            await publish(_EVENT_TYPE, payload)
        except Exception:  # noqa: BLE001 -- the block itself is already committed
            logger.exception(
                "notify_compliance_violation: tenant=%s rule=%s business_date=%s 알림 발행 실패",
                tenant_id,
                rule_code,
                business_date,
            )


__all__ = ["notify_violations", "PublishFn"]
