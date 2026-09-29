"""BR-22e(task-8075) -- Binance spot tick/lot/min-notional filter values.

Audit: docs/audits/AUDIT_2026-09-26_order_path.md F4/§1 (task-7978 finding)
-- `trading_mixin.py::place_order` only checked quantity/price for
>0/finite, with no PRICE_FILTER/LOT_SIZE/NOTIONAL pre-validation before the
exchange call, so a tick-misaligned price or a below-minimum order would
round-trip to Binance before being rejected there.

Value provenance (honest labeling, §10 principle): these are **DOC_ONLY**
-- read from the public Binance Spot `GET /api/v3/exchangeInfo` filter
documentation (github.com/binance/binance-spot-api-docs, `filters.md`,
`PRICE_FILTER.tickSize` / `LOT_SIZE.stepSize` / `NOTIONAL.minNotional`),
not a live `exchangeInfo` response fetched in this session -- same
DOC_ONLY convention as `kiwoom/capabilities.py` before a live snapshot
exists. Only BTCUSDT/ETHUSDT are populated (the two symbols this mixin's
tests exercise); a symbol with no entry here has no tick/lot/min-notional
check applied (`_validate_venue_limits` skips it) rather than guessing a
value never confirmed against a real Binance response -- a follow-up leaf
should populate the rest of the traded symbol set from a live
`exchangeInfo` call before relying on this table for those symbols.
"""

from __future__ import annotations

from decimal import Decimal

PRICE_TICK: dict[str, Decimal] = {
    "BTCUSDT": Decimal("0.01"),
    "ETHUSDT": Decimal("0.01"),
}

QTY_LOT: dict[str, Decimal] = {
    "BTCUSDT": Decimal("0.00001"),
    "ETHUSDT": Decimal("0.0001"),
}

MIN_NOTIONAL: dict[str, Decimal] = {
    "BTCUSDT": Decimal("5"),
    "ETHUSDT": Decimal("5"),
}
