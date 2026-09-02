"""LC-5 — 홀드 상태기계.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.5, §9 LC-5.

전이표(§4.5)를 그대로 코드화한다: `place`(생성) → PENDING, `capture`/
`release`/`expire`는 PENDING에서만 허용된다. `available ≥ amount`·
"계정 미동결" 가드는 실 잔액 조회(I/O)가 필요해 이 모듈의 책임이 아니다
(호출자가 `balance_rules.apply` 등으로 먼저 확인한다) — 여기서는 시각
비교만으로 판정 가능한 만료 가드만 강제한다. 순수 함수만: I/O·시계 직접
호출 금지, `now`는 항상 인자로 받는다.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from src.foundation.ledger.contracts.v1 import HoldState


class HoldEvent(str, Enum):
    PLACE = "PLACE"
    CAPTURE = "CAPTURE"
    RELEASE = "RELEASE"
    EXPIRE = "EXPIRE"


class IllegalHoldTransitionError(ValueError):
    """`LEDGER_HOLD_STATE_INVALID` — 전이표(§4.5)에 없는 (from, event) 조합."""


class HoldExpiredError(IllegalHoldTransitionError):
    """만료된 홀드를 `capture`하려는 시도(§4.5 "PENDING, capture, guard=미만료")."""


class HoldNotYetExpiredError(IllegalHoldTransitionError):
    """`expires_at`이 아직 지나지 않은 홀드를 `expire`하려는 시도."""


# (from, event) → to. 여기 없는 조합은 전부 거부(§4.5 "CAPTURED/RELEASED/EXPIRED, *, 거부").
_ALLOWED: dict[tuple[HoldState | None, HoldEvent], HoldState] = {
    (None, HoldEvent.PLACE): HoldState.PENDING,
    (HoldState.PENDING, HoldEvent.CAPTURE): HoldState.CAPTURED,
    (HoldState.PENDING, HoldEvent.RELEASE): HoldState.RELEASED,
    (HoldState.PENDING, HoldEvent.EXPIRE): HoldState.EXPIRED,
}


def transition(
    current: HoldState | None,
    event: HoldEvent,
    *,
    now: datetime,
    expires_at: datetime,
) -> HoldState:
    """`current`에서 `event`로의 전이를 판정한다. 허용되지 않으면
    `IllegalHoldTransitionError`(fail-closed). `now`/`expires_at`은 `capture`·
    `expire`의 만료 가드에만 쓰인다."""
    next_state = _ALLOWED.get((current, event))
    if next_state is None:
        raise IllegalHoldTransitionError(
            f"홀드 전이 불가: {current} --{event.value}--> (허용되지 않은 조합)"
        )

    if event is HoldEvent.CAPTURE and now > expires_at:
        raise HoldExpiredError(
            f"만료된 홀드는 capture할 수 없습니다: now={now.isoformat()} > "
            f"expires_at={expires_at.isoformat()}"
        )
    if event is HoldEvent.EXPIRE and now <= expires_at:
        raise HoldNotYetExpiredError(
            f"아직 만료되지 않은 홀드는 expire할 수 없습니다: now={now.isoformat()} <= "
            f"expires_at={expires_at.isoformat()}"
        )

    return next_state
