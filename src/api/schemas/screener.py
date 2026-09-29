"""UX-8(task-7773) — `POST /v1/foundation/screener/run` response schema.

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §2.2/§9 UX-6/UX-8.
The request body is `ScreenDefinition` itself (`src/foundation/screener/
contracts/v1.py`, UX-5) — no wrapper schema is introduced for it, since that
contract already carries every field (and validator: non-empty `universe`,
at least one filter) the endpoint needs. Only the response view is new here,
mirroring `ScreenRunPage` (UX-6, `application/run_screen.py`).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.screener.application.run_screen import ScreenRunPage

__all__ = ["ScreenResultRowView", "ScreenRunResultView"]


class ScreenResultRowView(BaseModel, frozen=True):
    instrument_id: UUID
    symbol: str
    venue: Venue
    values: dict[str, Decimal]


class ScreenRunResultView(BaseModel, frozen=True):
    rows: tuple[ScreenResultRowView, ...]
    total: int
    truncated: bool
    next_cursor: str | None

    @classmethod
    def from_page(cls, page: ScreenRunPage) -> ScreenRunResultView:
        return cls(
            rows=tuple(
                ScreenResultRowView(
                    instrument_id=row.instrument_id,
                    symbol=row.symbol,
                    venue=row.venue,
                    values=row.values,
                )
                for row in page.rows
            ),
            total=page.total,
            truncated=page.truncated,
            next_cursor=page.next_cursor,
        )
