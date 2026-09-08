"""BT-16b — ExperimentLedgerPort: append-only experiment ledger storage contract.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 BT-16;
docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.4 (AI-10 `adapters/
postgres_repository.py` -- append-only, WORM trigger). That adapter and its
migration are out of scope for this leaf (task-2426 DoD(a)) -- this file only
declares the Protocol a future adapter must implement, following the same
`conn`-takes-an-already-open-connection pattern as `src/foundation/ledger/
ports/journal_repository.py`.

Protocol declaration and `...` bodies only -- no I/O, no default implementation
(same rule as `ports/fill_simulator.py`/`ports/bar_source.py`, L26 DoD (b)).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import asyncpg

from src.foundation.backtest.vector.experiment_ledger import ExperimentLedgerEntry

__all__ = ["ExperimentLedgerPort"]


@runtime_checkable
class ExperimentLedgerPort(Protocol):
    async def append(self, conn: asyncpg.Connection, entry: ExperimentLedgerEntry) -> None: ...
