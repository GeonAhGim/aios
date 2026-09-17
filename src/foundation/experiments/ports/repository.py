"""AI-10 -- `ExperimentRepository` port.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.4 AI-10
(`adapters/postgres_repository.py + migration` -- append-only, WORM trigger
reuse). Follows the `src/foundation` convention (standard 71 §4): domain/
application code depends only on this `Protocol`, never on the concrete
asyncpg adapter in `adapters/postgres_repository.py`.

The `experiments` table is append-only (WORM) -- there is no update method,
only `append` and read-only lookups. A future leaf (AI-11
`application/{record,query,compare}.py`) is the only intended caller of
`append`; this leaf ships the port and its one concrete adapter, not the use
case.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from src.foundation.experiments.contracts.v1 import Experiment

__all__ = ["ExperimentRepository"]


class ExperimentRepository(Protocol):
    async def append(self, experiment: Experiment) -> None:
        """Insert one experiment row. `experiment.experiment_id` is always
        caller-generated (never DB-assigned) -- lineage validation
        (`domain/lineage.py::validate_new_experiment`) needs the id before
        the row exists, to check for self-parenting."""
        ...

    async def get(self, tenant_id: UUID, experiment_id: UUID) -> Experiment | None: ...

    async def find_by_reproducibility_key(
        self, tenant_id: UUID, reproducibility_key: str
    ) -> tuple[Experiment, ...]:
        """All experiments in `tenant_id` sharing `reproducibility_key`,
        oldest first. Empty tuple if none -- used both by AI-11's compare
        use case and by `domain/lineage.py`'s collision guard (a caller
        passes the first result, if any, as `existing_with_same_key`)."""
        ...
