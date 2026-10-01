"""LB-16 — Exchange balance source (adapters/exchange_balance_source.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9.3 LB-16.

Implements `ProviderBalanceSource` from `ports/exchange_balance_source.py`(LB-7) via
`ExchangeAdapter.get_balance` from `src/exchanges/common/adapter.py`.
The `connection_id` -> `ExchangeAdapter` instance mapping (credential resolution,
actual exchange-specific adapter creation) is not created by this leaf — that belongs
to `src/services/credential_resolver.py` responsibility, and here we only receive
pre-built adapter instances (FROZEN LIVE path closed, ADR-2026-08-29-E; this class
performs lookups only and does not handle credentials).

If `get_balance()` raises an exception (network error, auth failure, etc.), it
propagates as-is — FD-3.3 "lookup failure propagates as exception, never replaced
with empty list" (returning an empty list would conflate "no balance" with "lookup
failure").
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from src.data.models.trading import AccountBalance
from src.exchanges.common.adapter import ExchangeAdapter

__all__ = ["ExchangeBalanceSource", "UnknownConnectionError"]


class UnknownConnectionError(KeyError):
    """No adapter mapped for `connection_id` — wiring error (distinct from credential
    unregistration, which is a responsibility prior to adapter creation)."""


class ExchangeBalanceSource:
    """`ProviderBalanceSource` implementation — wraps a per-`connection_id` map of
    `ExchangeAdapter` instances."""

    def __init__(self, adapters: Mapping[UUID, ExchangeAdapter]) -> None:
        self._adapters = adapters

    async def balances(self, connection_id: UUID) -> list[AccountBalance]:
        adapter = self._adapters.get(connection_id)
        if adapter is None:
            raise UnknownConnectionError(connection_id)
        return await adapter.get_balance()
