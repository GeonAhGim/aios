"""Verified KR-equity `SymbolRegistry` test double for KIS `place_order()` tests.

task-8337 routed `KISTradingMixin._precheck_order` through
`SymbolRegistry.require_verified`, and the production 005930.KS snapshot is
registered `verified=False` (tick/lot not yet confirmed against the venue), so
any test that exercises the HTTP order path with 005930 must inject a
`verified=True` double or it stops at `OrderValidationError` before the
request is ever built. `tests/unit/exchanges/kis/test_client_order_id_
idempotency.py` carries the same double inline; this module is the shared
copy for the integration modules (adapter routing, capability matrix) whose
concern is dispatch, not verification-gating. Assign the *function* as the
adapter's `symbol_registry` attribute -- the mixin calls it with no arguments.
"""
from __future__ import annotations

from decimal import Decimal

from src.services.oms.domain.symbol_registry import SymbolRegistry


def verified_kr_equity_registry() -> SymbolRegistry:
    registry = SymbolRegistry()
    registry.register(
        "005930.KS",
        "kis",
        "005930",
        tick=Decimal("100"),
        lot=Decimal("1"),
        min_notional=Decimal("0"),
        quote_ccy="KRW",
        verified=True,
    )
    return registry
