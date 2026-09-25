"""RecordExperiment command -- AI-11.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.4 AI-11
`application/{record,query,compare}.py` (record/query/compare, supplying
agent-facing context), §9 AI-11 DoD (reproducibility-key identity).

This is the one caller `ports/repository.py::ExperimentRepository.append`'s
docstring names -- it resolves the two lookups `domain/lineage.py::
validate_new_experiment` needs (the parent row, and any prior experiment
sharing `candidate.reproducibility_key`) before calling it, then appends only
if that passes. `candidate` is always fully built by the caller (AI-9's
`generate_proposal`, a future backtest/sweep runner, etc.) -- this module
does not construct `Experiment` itself, matching AI-10's `contracts/v1.py`
owning validation of the value, not its assembly.
"""

from __future__ import annotations

from src.foundation.experiments.contracts.v1 import Experiment
from src.foundation.experiments.domain.lineage import validate_new_experiment
from src.foundation.experiments.ports.repository import ExperimentRepository

__all__ = ["record_experiment"]


async def record_experiment(repository: ExperimentRepository, candidate: Experiment) -> Experiment:
    """Validate `candidate` against the current ledger state, then append it.

    Both lookups run unconditionally (rather than only when `parent_id` is
    set / only when a same-key row might exist) because they are cheap
    indexed reads and `validate_new_experiment` itself already tells the two
    "not applicable" cases apart (`parent_id is None`, `existing_with_same_key
    is None`) -- duplicating that branching here would just be two copies of
    the same decision.
    """
    parent: Experiment | None = None
    if candidate.parent_id is not None:
        parent = await repository.get(candidate.tenant_id, candidate.parent_id)

    same_key = await repository.find_by_reproducibility_key(
        candidate.tenant_id, candidate.reproducibility_key
    )
    existing_with_same_key = same_key[0] if same_key else None

    validate_new_experiment(candidate, parent=parent, existing_with_same_key=existing_with_same_key)
    await repository.append(candidate)
    return candidate
