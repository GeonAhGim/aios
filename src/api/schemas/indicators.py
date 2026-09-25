"""IND-12 — `GET /v1/indicators` response schema + cursor validation.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.9 IND-12.

The cursor is the last item's name verbatim ("opaque string" convention —
like `positions.py` LB-19 stringifying `sequence_no` as-is — validate
format only, no separate encoding layer). Decode failure is a transport-
layer error, not a domain error, so we express it here as
`InvalidIndicatorCursorError` and let the global handler (EXCEPTION_MAP)
translate it into a VALIDATION_INVALID_FIELD envelope (same pattern as
LB-19 `InvalidCursorError`, raw HTTPException prohibited).
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from src.core.indicators.catalog.registry_tiers import CatalogEntry, Tier

__all__ = [
    "IndicatorListItemView",
    "IndicatorListView",
    "InvalidIndicatorCursorError",
    "decode_cursor",
]

# Charset allowed for indicator/script names — covers TA-Lib function names
# (uppercase alphanumeric) and future script-based indicator names
# (alphanumeric, _, ., :, -).
_CURSOR_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class InvalidIndicatorCursorError(ValueError):
    """The `cursor` query parameter is not in the format issued by this API."""


def decode_cursor(raw: str | None) -> str | None:
    """Return None early if absent. Reject if outside allowed charset or too long."""
    if raw is None or raw == "":
        return None
    if not _CURSOR_RE.match(raw):
        raise InvalidIndicatorCursorError(f"cursor 형식이 올바르지 않습니다: {raw!r}")
    return raw


class IndicatorListItemView(BaseModel):
    name: str
    tier: Tier
    category: str
    version: str
    hash: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]

    @classmethod
    def from_entry(cls, entry: CatalogEntry) -> IndicatorListItemView:
        return cls(
            name=entry.name,
            tier=entry.tier,
            category=entry.category,
            version=entry.version,
            hash=entry.entry_hash,
            inputs=entry.spec.inputs,
            outputs=entry.spec.outputs,
        )


class IndicatorListView(BaseModel):
    items: list[IndicatorListItemView]
