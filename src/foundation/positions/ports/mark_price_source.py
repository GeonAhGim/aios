"""LB-7 — 마크가격 공급 포트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9 LB-7.

domain/application은 이 Protocol만 알고, 실제 구현(adapters/candle_mark_price_source.py,
LB-14)은 모른다(71번 §4). 값을 못 구하면(스테일 포함) `0`으로 대체하지 않고
`None`을 반환한다 — 미실현 PnL은 마크 없이 계산하지 않는다(§4 domain/pnl.py).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime

from src.data.models.base import Money


@runtime_checkable
class MarkPriceSource(Protocol):
    async def mark(self, position_key: str, at: AwareDatetime) -> Money | None:
        """`at` 시점 기준 최신 마크가격. 스테일하거나 없으면 `None`
        (POS_MARK_STALE — 호출자가 "미실현 None 유지"로 처리)."""
        ...
