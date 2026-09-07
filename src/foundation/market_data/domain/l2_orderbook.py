"""RD-19 — L2 호가창 상태 갱신(순수).

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3·D5, task-1766.

거래소 WS 메시지 파싱(`adapters/ingest/*_l2.py`)과 시퀀스 갭 판정·재동기화
호출(`exchanges/common/ws_session.py`, 재사용 — 재구현 아님)은 이 모듈의
책임이 아니다. 이 모듈은 이미 파싱된 스냅샷/증분만 받아 로컬 호가창
상태를 갱신하는 순수 값 객체만 제공한다 — I/O 없음, 시계 없음(`as_of`는
호출자가 넘긴다).

증분 적용 규칙: `qty == 0`인 레벨은 삭제, 그 외는 upsert(거래소 공통
관례). 음수 수량은 거래소 프로토콜 위반이므로 조용히 무시하지 않고
`NegativeQuantityError`로 fail-closed 표면화한다.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

__all__ = [
    "NegativeQuantityError",
    "L2Snapshot",
    "L2Diff",
    "OrderBookState",
]


class NegativeQuantityError(ValueError):
    """음수 수량 호가 레벨 — 거래소 프로토콜 위반, 조용히 무시하지 않는다."""


@dataclass(frozen=True)
class L2Snapshot:
    """전체 호가창 스냅샷(REST 재동기화, Upbit의 매 틱 전체 푸시 등)."""

    sequence: int
    as_of: datetime
    bids: Mapping[Decimal, Decimal]
    asks: Mapping[Decimal, Decimal]


@dataclass(frozen=True)
class L2Diff:
    """증분 갱신(가격→수량). `sequence`는 이 증분 적용 후의 최종 시퀀스."""

    sequence: int
    as_of: datetime
    bid_updates: tuple[tuple[Decimal, Decimal], ...]
    ask_updates: tuple[tuple[Decimal, Decimal], ...]


def _validate_snapshot_side(levels: Mapping[Decimal, Decimal]) -> None:
    if any(qty < 0 for qty in levels.values()):
        raise NegativeQuantityError("스냅샷 호가 수량은 0 이상이어야 한다")


def _validate_diff_side(updates: tuple[tuple[Decimal, Decimal], ...]) -> None:
    if any(qty < 0 for _, qty in updates):
        raise NegativeQuantityError("증분 호가 수량은 0 이상이어야 한다")


def _apply_side(
    side: dict[Decimal, Decimal], updates: tuple[tuple[Decimal, Decimal], ...]
) -> dict[Decimal, Decimal]:
    result = dict(side)
    for price, qty in updates:
        if qty == 0:
            result.pop(price, None)
        else:
            result[price] = qty
    return result


@dataclass(frozen=True)
class OrderBookState:
    """한 시점의 로컬 호가창 상태. 불변 — 갱신은 항상 새 인스턴스를 반환한다."""

    sequence: int
    as_of: datetime
    bids: Mapping[Decimal, Decimal]
    asks: Mapping[Decimal, Decimal]

    @staticmethod
    def from_snapshot(snapshot: L2Snapshot) -> OrderBookState:
        _validate_snapshot_side(snapshot.bids)
        _validate_snapshot_side(snapshot.asks)
        return OrderBookState(
            sequence=snapshot.sequence,
            as_of=snapshot.as_of,
            bids=dict(snapshot.bids),
            asks=dict(snapshot.asks),
        )

    def apply_diff(self, diff: L2Diff) -> OrderBookState:
        _validate_diff_side(diff.bid_updates)
        _validate_diff_side(diff.ask_updates)
        return OrderBookState(
            sequence=diff.sequence,
            as_of=diff.as_of,
            bids=_apply_side(dict(self.bids), diff.bid_updates),
            asks=_apply_side(dict(self.asks), diff.ask_updates),
        )

    def top_n(self, n: int) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
        """장기 보존 다운샘플용 상위 N호가. bids는 가격 내림차순, asks는 오름차순."""
        top_bids = sorted(self.bids.items(), key=lambda kv: kv[0], reverse=True)[:n]
        top_asks = sorted(self.asks.items(), key=lambda kv: kv[0])[:n]
        return top_bids, top_asks
