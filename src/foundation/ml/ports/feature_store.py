"""AI-19 -- FeatureStore port.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-19
(`ml ports + parquet_feature_store` ...). Application/domain code depends
only on this `Protocol`, never on the concrete pyarrow adapter in
`adapters/parquet_feature_store.py` (standard 71 §4, same convention as
`src/foundation/experiments/ports/repository.py`).

A feature store persists engineered values for one `FeatureSpec` (AI-18's
`contracts/v1.py`) as rows of `(entity_id, as_of, value)`, one immutable
partition per `(feature_id, day)` -- the same access pattern DC-23's
`TickParquetStorage` uses for tick/quote partitions. Local Parquet I/O is
synchronous throughout this repo (`warm_parquet.py`, `tick_parquet.py`
both expose plain `def`, never `async def`), so this port follows suit --
unlike `ports/model_registry.py`, which is Postgres-backed and async.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple, Protocol

from src.foundation.ml.contracts.v1 import FeatureSpec

__all__ = ["FeatureValue", "FeatureStorePort"]


class FeatureValue(NamedTuple):
    """One feature observation. `value` is always the string
    representation of the raw value -- the caller casts it back per
    `FeatureSpec.dtype` (same discipline as `TickParquetStorage`, which
    stores every column as a string regardless of its DTO's native type)."""

    entity_id: str
    as_of: datetime
    value: str


class FeatureStorePort(Protocol):
    def write_batch(self, spec: FeatureSpec, day: date, rows: Iterable[FeatureValue]) -> Path:
        """Publish one immutable partition for `spec.feature_id`/`day`.
        Retrying with byte-identical rows succeeds (idempotent,
        standard-105); retrying with different rows for an already-
        published partition fails closed."""
        ...

    def read_batch(self, spec: FeatureSpec, day: date) -> Iterator[FeatureValue]:
        """Yield rows for `spec.feature_id`/`day`. Raises
        `FileNotFoundError` if the partition was never published --
        distinct from a published but empty day."""
        ...
