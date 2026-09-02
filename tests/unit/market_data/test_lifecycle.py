"""LA-7 — lifecycle 상태기계 단위 테스트(§4.2 전이표 전수).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.1 test_lifecycle.py.

§4.2 표는 5개 행이지만 `LISTED/SUSPENDED | DELIST` 행은 두 개의
(state, event) 쌍으로 펼쳐지므로 실제 허용 조합은 6개다. 4개 상태 ×
5개 이벤트 = 20 조합을 전수 검사해 6개는 표대로, 나머지 14개(DELISTED
발 전이 전부 포함)는 전부 거부됨을 증명한다.
"""
from __future__ import annotations

import pytest

from src.foundation.market_data.contracts.v1 import SymbolStatus
from src.foundation.market_data.domain.reference.lifecycle import (
    LifecycleEvent,
    LifecycleTransitionError,
    transition,
)

_EVENTS: tuple[LifecycleEvent, ...] = ("LIST", "SUSPEND", "RESUME", "DELIST", "RENAME")

_ALLOWED: dict[tuple[SymbolStatus, LifecycleEvent], SymbolStatus] = {
    (SymbolStatus.PENDING, "LIST"): SymbolStatus.LISTED,
    (SymbolStatus.LISTED, "SUSPEND"): SymbolStatus.SUSPENDED,
    (SymbolStatus.SUSPENDED, "RESUME"): SymbolStatus.LISTED,
    (SymbolStatus.LISTED, "DELIST"): SymbolStatus.DELISTED,
    (SymbolStatus.SUSPENDED, "DELIST"): SymbolStatus.DELISTED,
    (SymbolStatus.LISTED, "RENAME"): SymbolStatus.LISTED,
}


@pytest.mark.parametrize("state", list(SymbolStatus))
@pytest.mark.parametrize("event", _EVENTS)
def test_transition_matches_table(state: SymbolStatus, event: LifecycleEvent) -> None:
    key = (state, event)
    if key in _ALLOWED:
        assert transition(state, event) == _ALLOWED[key]
    else:
        with pytest.raises(LifecycleTransitionError):
            transition(state, event)


def test_delisted_rejects_every_event() -> None:
    for event in _EVENTS:
        with pytest.raises(LifecycleTransitionError):
            transition(SymbolStatus.DELISTED, event)
