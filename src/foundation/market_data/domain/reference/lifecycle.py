"""LA-7 — 심볼 생애주기 상태기계(순수 함수).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-7, §4.2, §9.2 LA-7.

`SymbolStatus`는 LA-1 계약(contracts/v1)을 그대로 쓴다(재정의 금지).
`LifecycleEvent`는 `LifecycleEventCommand.event` 필드와 동일한 Literal을
그대로 옮긴 것이다 — 새 의미를 갖는 별도 타입이 아니다.

§4.2 표의 가드(열린 포지션 0 또는 강제 플래그, `new_venue_symbol` 미사용)는
리포지토리 조회가 필요해 순수 함수 범위 밖이다. 이 함수는 상태×이벤트만으로
정해지는 전이 가능 여부만 판정하고, 가드는 application 계층
(`register_instrument.apply_lifecycle_event`, 후속 리프)의 몫이다.
"""
from __future__ import annotations

from typing import Literal

from src.foundation.market_data.contracts.v1 import SymbolStatus

__all__ = ["LifecycleEvent", "LifecycleTransitionError", "transition"]

LifecycleEvent = Literal["LIST", "SUSPEND", "RESUME", "DELIST", "RENAME"]

_TRANSITIONS: dict[tuple[SymbolStatus, LifecycleEvent], SymbolStatus] = {
    (SymbolStatus.PENDING, "LIST"): SymbolStatus.LISTED,
    (SymbolStatus.LISTED, "SUSPEND"): SymbolStatus.SUSPENDED,
    (SymbolStatus.SUSPENDED, "RESUME"): SymbolStatus.LISTED,
    (SymbolStatus.LISTED, "DELIST"): SymbolStatus.DELISTED,
    (SymbolStatus.SUSPENDED, "DELIST"): SymbolStatus.DELISTED,
    (SymbolStatus.LISTED, "RENAME"): SymbolStatus.LISTED,
}


class LifecycleTransitionError(ValueError):
    """`outcome=DENIED` — §4.2 표에 없는 (state, event) 조합(DELISTED는 전부 거부)."""


def transition(state: SymbolStatus, event: LifecycleEvent) -> SymbolStatus:
    """§4.2 상태기계. 표에 없는 전이는 `LifecycleTransitionError`(fail-closed)."""
    try:
        return _TRANSITIONS[(state, event)]
    except KeyError as exc:
        raise LifecycleTransitionError(
            f"허용되지 않는 전이: {state.value} + {event}"
        ) from exc
