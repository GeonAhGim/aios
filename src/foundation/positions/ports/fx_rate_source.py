"""LB-7 — FX rate supply port.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9 LB-7.

The domain/application layer knows only this Protocol; the actual implementation
(adapters/fx_rate_source.py, LB-14) remains opaque (§4). Returns `None` instead of
defaulting to `0` — triangular conversion is also left to the caller (§4
domain/fx.py `FxRateMissingError`).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime

from src.data.models.base import Currency, FXRate


@runtime_checkable
class FxRateSource(Protocol):
    async def rate(self, base: Currency, quote: Currency, at: AwareDatetime) -> FXRate | None:
        """FX rate for `base/quote` at the given `at` timestamp.

        Returns `None` if not found (POS_FX_RATE_MISSING).
        """
        ...
