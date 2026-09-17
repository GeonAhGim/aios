"""CompareExperiments query -- AI-11.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.4 AI-11
`application/{record,query,compare}.py` -- the compare third, §9 AI-11 DoD
(reproducibility-key identity).

`domain/lineage.py::validate_new_experiment` is the only thing that normally
keeps every row sharing one `reproducibility_key` at byte-identical
`inputs_hash` (`ReproducibilityKeyCollisionError`) -- but that guard runs on
`record.py::record_experiment`'s write path only. A row written some other
way (a bypass, a bug upstream of `record_experiment`) could still land in
the WORM ledger with a diverging `inputs_hash` under the same key. This
module is the read-side counterpart: before it will present a set of
experiments as "reruns of the same thing", it re-checks that identity
itself, rather than trusting the write-path guard already ran.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from src.foundation.experiments.application.query import get_experiment
from src.foundation.experiments.contracts.v1 import Experiment
from src.foundation.experiments.ports.repository import ExperimentRepository

__all__ = [
    "ReproducibilityKeyMismatchError",
    "CorruptedReproductionSetError",
    "NoReproductionsFoundError",
    "ExperimentComparison",
    "compare_experiments",
    "compare_by_reproducibility_key",
]


class ReproducibilityKeyMismatchError(ValueError):
    """`compare_experiments` was asked to compare experiments that do not
    all share one `reproducibility_key` -- comparing across different keys
    is not "did this reproduce", just an arbitrary diff, which is not this
    module's job (call `get_experiment` on each id directly for that)."""

    def __init__(self, experiment_id: UUID, expected_key: str, actual_key: str) -> None:
        self.experiment_id = experiment_id
        self.expected_key = expected_key
        self.actual_key = actual_key
        super().__init__(
            f"experiment {experiment_id!r} has reproducibility_key {actual_key!r}, "
            f"expected {expected_key!r}"
        )


class CorruptedReproductionSetError(ValueError):
    """Two experiments share `reproducibility_key` but disagree on
    `inputs_hash` -- see module docstring. This can only happen if a row was
    written without going through `record.py::record_experiment`'s lineage
    guard; comparing their metrics as if they were the same rerun would be
    silently wrong, so this module refuses instead."""

    def __init__(self, reproducibility_key: str, inputs_hashes: frozenset[str]) -> None:
        self.reproducibility_key = reproducibility_key
        self.inputs_hashes = inputs_hashes
        super().__init__(
            f"reproducibility_key {reproducibility_key!r} spans divergent inputs_hash "
            f"values {sorted(inputs_hashes)!r} -- not a legitimate reproduction set"
        )


class NoReproductionsFoundError(LookupError):
    """`compare_by_reproducibility_key` found zero experiments under
    `reproducibility_key` in this tenant."""

    def __init__(self, tenant_id: UUID, reproducibility_key: str) -> None:
        self.tenant_id = tenant_id
        self.reproducibility_key = reproducibility_key
        super().__init__(
            f"no experiments found for reproducibility_key {reproducibility_key!r} "
            f"in tenant {tenant_id!r}"
        )


@dataclass(frozen=True)
class ExperimentComparison:
    """Agent-context-friendly comparison result. `inputs_hash` is the single
    value every member of `experiments` was checked to share -- its presence
    here (rather than requiring the caller re-derive it) is the DoD's
    "reproducibility key identity" made visible in the return shape."""

    reproducibility_key: str
    inputs_hash: str
    experiments: tuple[Experiment, ...]
    metrics_by_experiment: dict[UUID, dict[str, Any]]
    metric_keys: tuple[str, ...]


def _build_comparison(
    reproducibility_key: str, experiments: Sequence[Experiment]
) -> ExperimentComparison:
    inputs_hashes = frozenset(e.inputs_hash for e in experiments)
    if len(inputs_hashes) > 1:
        raise CorruptedReproductionSetError(reproducibility_key, inputs_hashes)

    ordered = tuple(experiments)
    metrics_by_experiment = {e.experiment_id: e.metrics for e in ordered}
    metric_keys = tuple(sorted({key for e in ordered for key in e.metrics}))
    return ExperimentComparison(
        reproducibility_key=reproducibility_key,
        inputs_hash=next(iter(inputs_hashes)),
        experiments=ordered,
        metrics_by_experiment=metrics_by_experiment,
        metric_keys=metric_keys,
    )


async def compare_experiments(
    repository: ExperimentRepository, tenant_id: UUID, experiment_ids: Sequence[UUID]
) -> ExperimentComparison:
    """Fetch and compare a caller-chosen set of experiment ids. Raises
    unless every id resolves (`ExperimentNotFoundError`, from
    `get_experiment`) and every one shares both `reproducibility_key`
    (`ReproducibilityKeyMismatchError`) and `inputs_hash`
    (`CorruptedReproductionSetError`)."""
    if len(experiment_ids) < 2:
        raise ValueError("compare_experiments requires at least two experiment_ids")

    experiments = [
        await get_experiment(repository, tenant_id, experiment_id)
        for experiment_id in experiment_ids
    ]
    reproducibility_key = experiments[0].reproducibility_key
    for experiment in experiments[1:]:
        if experiment.reproducibility_key != reproducibility_key:
            raise ReproducibilityKeyMismatchError(
                experiment.experiment_id, reproducibility_key, experiment.reproducibility_key
            )

    return _build_comparison(reproducibility_key, experiments)


async def compare_by_reproducibility_key(
    repository: ExperimentRepository, tenant_id: UUID, reproducibility_key: str
) -> ExperimentComparison:
    """Fetch and compare every experiment sharing `reproducibility_key` in
    `tenant_id` -- the common case (agent asks "how did all reruns of this
    do"), vs. `compare_experiments`'s caller-chosen id list."""
    experiments = await repository.find_by_reproducibility_key(tenant_id, reproducibility_key)
    if not experiments:
        raise NoReproductionsFoundError(tenant_id, reproducibility_key)

    return _build_comparison(reproducibility_key, experiments)
