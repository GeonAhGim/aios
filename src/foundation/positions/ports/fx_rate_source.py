"""LB-7 — 환율 공급 포트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9 LB-7.

domain/application은 이 Protocol만 알고, 실제 구현(adapters/fx_rate_source.py, LB-14)은
모른다(71번 §4). 없으면 `0`으로 대체하지 않고 `None`을 반환한다 — 삼각환산도
호출자가 하지 않는다(§4 domain/fx.py `FxRateMissingError`).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime

from src.data.models.base import Currency, FXRate


@runtime_checkable
class FxRateSource(Protocol):
    async def rate(self, base: Currency, quote: Currency, at: AwareDatetime) -> FXRate | None:
        """`at` 시점 `base/quote` 환율. 없으면 `None`(POS_FX_RATE_MISSING)."""
        ...
