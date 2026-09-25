"""Segregation-of-duty port -- core-owned contract for RATCHET-2 core-no-io.

`src/core` may not import `src/foundation` (`.importlinter` `core-no-io`), but
`assert_actor_not_counterparty()` -- the single definition of "actor !=
counterparty" (PLT-43, see
`src/foundation/trust/domain/rules/segregation_of_duty.py`) -- is the only
check `core/security/break_glass.py` has for self-approval short of the DB
CHECK constraint. Port/adapter inversion resolves this without duplicating
the primitive: core defines the contract (`SegregationOfDutyChecker`
Protocol + `SegregationOfDutyViolation`) here, the concrete implementation
stays in `foundation.trust` (which is allowed to import `core`), and the
composition root (`src/api/routers/admin_break_glass.py`) injects the
foundation implementation into the core call.
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Protocol


class SegregationOfDutyViolation(Exception):
    """The same subject tried to act as both the actor and counterparty of
    an action."""

    def __init__(self, actor_id: Hashable, action: str) -> None:
        super().__init__(
            f"{action}: actor({actor_id!r})는 자기 자신의 counterparty가 될 수 없습니다."
        )
        self.actor_id = actor_id
        self.action = action


class SegregationOfDutyChecker(Protocol):
    def __call__(
        self, actor_id: Hashable, counterparty_id: Hashable | None, *, action: str
    ) -> None: ...


def assert_actor_not_counterparty(
    actor_id: Hashable, counterparty_id: Hashable | None, *, action: str
) -> None:
    """Rejects if actor_id equals this action's counterparty_id.

    Pure comparison (no I/O), so the primitive itself lives here in core
    rather than behind the `SegregationOfDutyChecker` port -- only the
    injection boundary (`core/security/break_glass.py`) needs the port.
    `foundation.trust.domain.rules.segregation_of_duty` re-exports this for
    the call sites that are allowed to import foundation directly.

    If `counterparty_id` is `None` (no one has taken that role yet -- e.g.
    before the first DUAL-approval signature, before a mandate draft
    proposal), there is nothing to compare against, so it passes through.
    """
    if counterparty_id is not None and actor_id == counterparty_id:
        raise SegregationOfDutyViolation(actor_id, action)


__all__ = [
    "SegregationOfDutyChecker",
    "SegregationOfDutyViolation",
    "assert_actor_not_counterparty",
]
