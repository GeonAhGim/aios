"""AI-19 -- model registry rules (pure, no I/O).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-19
(`postgres_model_registry`), §9 AI-19 DoD ("lineage storage").

`adapters/postgres_model_registry.py::PostgresModelRegistry.register` calls
this after an `INSERT ... ON CONFLICT (model_id, version) DO NOTHING`
finds an existing row -- same "domain decides, adapter only executes"
split as `src/foundation/experiments/domain/lineage.py::validate_new_
experiment` (this leaf's sibling in the same spec).
"""

from __future__ import annotations

from src.foundation.ml.contracts.v1 import ModelCard

__all__ = ["ModelHashMismatchError", "validate_new_registration"]


class ModelHashMismatchError(ValueError):
    """`model_id`+`version` is already registered under a different
    `model_hash`. Re-registering the same (model_id, version) with a
    different hash is either a build-reproducibility bug or an attempt to
    silently swap a promoted model's weights -- rejected fail-closed
    (standard-105: identical retries succeed, conflicting content does
    not) instead of overwriting the existing row."""

    def __init__(
        self, model_id: str, version: str, existing_hash: str, candidate_hash: str
    ) -> None:
        self.model_id = model_id
        self.version = version
        self.existing_hash = existing_hash
        self.candidate_hash = candidate_hash
        super().__init__(
            f"model {model_id!r} version {version!r} is already registered with "
            f"model_hash={existing_hash!r}; cannot re-register with a different "
            f"model_hash={candidate_hash!r}"
        )


def validate_new_registration(candidate: ModelCard, *, existing: ModelCard | None) -> None:
    """Call with `existing` set to the row already stored under
    `candidate.model_id`/`candidate.version`, or `None` if this is the
    first registration. Raises `ModelHashMismatchError` unless the hashes
    match; a matching hash means the caller is safely retrying an
    already-applied registration."""
    if existing is not None and existing.model_hash != candidate.model_hash:
        raise ModelHashMismatchError(
            candidate.model_id, candidate.version, existing.model_hash, candidate.model_hash
        )
