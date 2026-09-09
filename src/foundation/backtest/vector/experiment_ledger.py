"""BT-16b — experiment ledger record model (grid sweep leaf of AI-10).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 BT-16
("experiment ledger record"). The full AI-10 contract (`docs/specs/
L4_ai_research_strategy_factory_v1.0.md` §2.4 `Experiment{experiment_id,
reproducibility_key, kind, inputs_hash, metrics, artifacts, parent_id,
created_by}`, append-only WORM storage + migration) is out of scope for this
leaf (task-2426 DoD(a) — a new alembic head would collide with 1942/1943/2351/
2357, which already occupy the migration chain serially). This module only
covers the part BT-16's grid sweep needs right now: a pure record identifying
one parameter combo inside a grid run and its reproducibility key.

The reproducibility key is never computed here from scratch — it delegates to
BT-9 `domain/reproducibility.py::reproducibility_key()` unchanged (task-2426
DoD(b): defining a new hash function is a rejection). BT-9's four inputs
(`script_hash`/`data_lineage_hash`/`rollup_version`/`config`) have no seed
slot, so to make the ledger key sensitive to the run's `seed` (DoD(c)), this
module folds `seed` into the `script_hash` string it hands to BT-9
(`f"{script_hash}:seed={seed}"`) before delegating -- that is plain string
composition, not a new hash algorithm; BT-9 does 100% of the actual hashing.
This mirrors what a real DSL script already does when a seed literal is part
of the script source: the seed is naturally absorbed into `script_hash`
because it changes the script's compiled bytes.

Pure module -- no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.backtest.domain.reproducibility import reproducibility_key

__all__ = ["ExperimentLedgerEntry", "record_grid_entry"]


@dataclass(frozen=True, slots=True)
class ExperimentLedgerEntry:
    """One grid-sweep combo's ledger row. `combo_index` is the combo's 0-based
    position in this run's iteration order (the eventual append-only storage,
    AI-10, orders rows by arrival -- this field lets a caller reconstruct that
    order even before a real store exists)."""

    combo_key: str
    combo_index: int
    seed: int
    reproducibility_key: str

    def __post_init__(self) -> None:
        if not self.combo_key.strip():
            raise ValueError("ExperimentLedgerEntry.combo_key가 비어 있습니다")
        if self.combo_index < 0:
            raise ValueError(f"combo_index는 0 이상이어야 한다: {self.combo_index}")


def record_grid_entry(
    *,
    combo_key: str,
    combo_index: int,
    script_hash: str,
    data_lineage_hash: str,
    rollup_version: str,
    config: BacktestConfigV2,
    seed: int,
) -> ExperimentLedgerEntry:
    """Builds one combo's ledger entry. Same `(script_hash, data_lineage_hash,
    rollup_version, config, seed)` always yields a byte-identical
    `reproducibility_key` (BT-9's own determinism guarantee); changing only
    `seed` always changes it (seed is folded into the `script_hash` BT-9
    hashes, and BT-9 hashes every input it receives)."""
    key = reproducibility_key(
        script_hash=f"{script_hash}:seed={seed}",
        data_lineage_hash=data_lineage_hash,
        rollup_version=rollup_version,
        config=config,
    )
    return ExperimentLedgerEntry(
        combo_key=combo_key, combo_index=combo_index, seed=seed, reproducibility_key=key,
    )
