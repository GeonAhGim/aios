"""AI-19 -- ModelRegistry port.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-19
(`ml ports + postgres_model_registry` ...), §9 AI-19 DoD ("lineage
storage"). Application/domain code depends only on this `Protocol`, never
on the concrete asyncpg adapter in `adapters/postgres_model_registry.py`
(standard 71 §4, same convention as `src/foundation/experiments/ports/
repository.py`).
"""

from __future__ import annotations

from typing import Protocol

from src.foundation.ml.contracts.v1 import ModelCard

__all__ = ["ModelRegistryPort"]


class ModelRegistryPort(Protocol):
    async def register(self, card: ModelCard) -> ModelCard:
        """Durably store `card`, including its `train_data_lineage` (AI-19
        DoD: "lineage storage"). Idempotent: registering the same
        `(model_id, version)` again with an identical `model_hash` returns
        the already-stored card; a different `model_hash` under the same
        `(model_id, version)` raises `domain.registry_rules.
        ModelHashMismatchError` instead of overwriting (fail-closed)."""
        ...

    async def get(self, model_id: str, version: str) -> ModelCard | None: ...

    async def latest(self, model_id: str) -> ModelCard | None:
        """The most recently trained registration for `model_id` (ordered
        by `trained_at`, then by registration order as a tiebreaker), or
        `None` if `model_id` has no registrations."""
        ...
