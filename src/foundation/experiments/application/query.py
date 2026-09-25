"""ExperimentContext read use cases -- AI-11.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.4 AI-11
`application/{record,query,compare}.py` -- the query third: the read paths
an agent-facing caller (a future AI-14 `research_tools` delegation, or
AI-17's router) uses to supply experiment context back to a research agent.
All three functions here are read-only and never mutate the WORM ledger --
`record.py::record_experiment` is the only writer.
"""

from __future__ import annotations

from uuid import UUID

from src.foundation.experiments.contracts.v1 import Experiment
from src.foundation.experiments.ports.repository import ExperimentRepository

__all__ = [
    "ExperimentNotFoundError",
    "get_experiment",
    "list_reproductions",
    "get_lineage_chain",
]


class ExperimentNotFoundError(LookupError):
    """`experiment_id` does not resolve under `tenant_id` -- same "wrong
    tenant reads as missing" posture as `ExperimentRepository.get`'s own
    `WHERE tenant_id = $1` clause (no cross-tenant existence leak)."""

    def __init__(self, tenant_id: UUID, experiment_id: UUID) -> None:
        self.tenant_id = tenant_id
        self.experiment_id = experiment_id
        super().__init__(f"experiment {experiment_id!r} not found for tenant {tenant_id!r}")


async def get_experiment(
    repository: ExperimentRepository, tenant_id: UUID, experiment_id: UUID
) -> Experiment:
    experiment = await repository.get(tenant_id, experiment_id)
    if experiment is None:
        raise ExperimentNotFoundError(tenant_id, experiment_id)
    return experiment


async def list_reproductions(
    repository: ExperimentRepository, tenant_id: UUID, reproducibility_key: str
) -> tuple[Experiment, ...]:
    """Every experiment sharing `reproducibility_key`, oldest first -- a thin
    pass-through to the port (`find_by_reproducibility_key` already returns
    the empty tuple for "none", no wrapping error needed)."""
    return await repository.find_by_reproducibility_key(tenant_id, reproducibility_key)


async def get_lineage_chain(
    repository: ExperimentRepository, tenant_id: UUID, experiment_id: UUID
) -> tuple[Experiment, ...]:
    """Root-first ancestor chain ending at `experiment_id` itself.

    Walking `parent_id` upward terminates by construction: every row is
    inserted after its parent already exists (`domain/lineage.py::
    validate_new_experiment`'s `DanglingParentError` guard, backed by the
    migration's self-referencing FK) into a WORM table with no UPDATE path,
    so a later insert can never retroactively point an ancestor's `parent_id`
    at one of its own descendants -- a cycle cannot be constructed. No
    max-depth guard is needed for the same reason a WORM table needs no
    "detect the impossible" check elsewhere in this leaf.
    """
    chain: list[Experiment] = [await get_experiment(repository, tenant_id, experiment_id)]
    while chain[-1].parent_id is not None:
        chain.append(await get_experiment(repository, tenant_id, chain[-1].parent_id))
    chain.reverse()
    return tuple(chain)
