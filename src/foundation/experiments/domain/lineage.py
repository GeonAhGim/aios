"""AI-10 -- experiment lineage rules (pure, no I/O).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.4 AI-10
`domain/lineage.py` ("parent-child lineage / reproducibility-key rules").

The ledger is append-only (WORM, `adapters/postgres_repository.py` +
migration) -- a bad row can never be corrected after insert, so these checks
must run *before* `ExperimentRepository.append()`, not after. Two
invariants:

1. If `candidate.parent_id` is set, the referenced parent must actually
   exist, be found under the same tenant, and its `experiment_id` must
   match `candidate.parent_id` -- a caller cannot append a child pointing at
   a dangling or cross-tenant parent. `parent_id != experiment_id` (no
   self-parent) is already enforced by the contract itself
   (`contracts/v1.py::Experiment._check_invariants`), not repeated here.
2. If another experiment with the same `reproducibility_key` already exists
   in the same tenant, its `inputs_hash` must be byte-identical to
   `candidate.inputs_hash` -- by definition, sharing a reproducibility key
   means "re-running this yields the same result", so two rows sharing a
   key with different inputs are either a hash collision or a computation
   bug, never a legitimate independent re-run.
"""

from __future__ import annotations

from src.foundation.experiments.contracts.v1 import Experiment

__all__ = [
    "LineageError",
    "DanglingParentError",
    "CrossTenantParentError",
    "ReproducibilityKeyCollisionError",
    "validate_new_experiment",
]


class LineageError(ValueError):
    """Common base for experiment lineage rule violations."""


class DanglingParentError(LineageError):
    """`candidate.parent_id` does not resolve to an existing experiment."""

    def __init__(self, parent_id: object) -> None:
        self.parent_id = parent_id
        super().__init__(f"parent_id {parent_id!r} does not reference an existing experiment")


class CrossTenantParentError(LineageError):
    """`candidate` and its resolved parent belong to different tenants."""

    def __init__(self, candidate_tenant: object, parent_tenant: object) -> None:
        self.candidate_tenant = candidate_tenant
        self.parent_tenant = parent_tenant
        super().__init__(
            f"parent tenant {parent_tenant!r} does not match candidate tenant {candidate_tenant!r}"
        )


class ReproducibilityKeyCollisionError(LineageError):
    """Same `reproducibility_key`, different `inputs_hash` -- a hash
    collision or a computation bug, never a legitimate independent
    re-run."""

    def __init__(
        self, reproducibility_key: str, existing_inputs_hash: str, candidate_inputs_hash: str
    ) -> None:
        self.reproducibility_key = reproducibility_key
        self.existing_inputs_hash = existing_inputs_hash
        self.candidate_inputs_hash = candidate_inputs_hash
        super().__init__(
            f"reproducibility_key {reproducibility_key!r} collides with a different "
            f"inputs_hash (existing={existing_inputs_hash!r}, candidate={candidate_inputs_hash!r})"
        )


def validate_new_experiment(
    candidate: Experiment,
    *,
    parent: Experiment | None,
    existing_with_same_key: Experiment | None,
) -> None:
    """Call before `ExperimentRepository.append(candidate)`.

    `parent` must be the caller's lookup result for `candidate.parent_id`
    (`None` both when `candidate.parent_id is None` and when the lookup
    found nothing -- the two cases are told apart below via
    `candidate.parent_id`). `existing_with_same_key` is any prior experiment
    sharing `candidate.reproducibility_key` in the same tenant, or `None` if
    this is the first.
    """
    if candidate.parent_id is not None:
        if parent is None:
            raise DanglingParentError(candidate.parent_id)
        if parent.experiment_id != candidate.parent_id:
            raise LineageError(
                f"lookup mismatch: expected parent {candidate.parent_id!r}, "
                f"got {parent.experiment_id!r}"
            )
        if parent.tenant_id != candidate.tenant_id:
            raise CrossTenantParentError(candidate.tenant_id, parent.tenant_id)

    if existing_with_same_key is not None:
        if existing_with_same_key.inputs_hash != candidate.inputs_hash:
            raise ReproducibilityKeyCollisionError(
                candidate.reproducibility_key,
                existing_with_same_key.inputs_hash,
                candidate.inputs_hash,
            )
