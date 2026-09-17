"""AI-19 -- Parquet-backed feature store adapter.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-19
(`parquet_feature_store` + `ml ports`), §9 AI-19 DoD ("lineage storage").

Reuses DC-23's `TickParquetStorage` idempotent-write discipline verbatim
(atomic hard link + digest-checked retry) rather than reinventing it --
same standard-105 file-store pattern: identical retries succeed, a
conflicting retry for an already-published partition fails closed.

Layout: `<root>/<feature_id>/<YYYY-MM-DD>/values.parquet`. All three
columns (`entity_id`, `as_of`, `value`) are stored as strings -- `value`'s
native type varies per `FeatureSpec.dtype` (float/int/bool/category), so no
single pyarrow type fits every feature; string columns keep every dtype
byte-identical on round trip (same trade-off `warm_parquet.py` documents
for its own columns).
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Iterator
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import pyarrow as pa
import pyarrow.parquet as pq

from src.foundation.ml.contracts.v1 import FeatureSpec
from src.foundation.ml.domain.feature_values import validate_feature_value
from src.foundation.ml.ports.feature_store import FeatureValue

__all__ = ["ParquetFeatureStore"]

_SCHEMA = pa.schema([("entity_id", pa.string()), ("as_of", pa.string()), ("value", pa.string())])


def _digest(path: Path) -> bytes:
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.digest()


class ParquetFeatureStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _path(self, spec: FeatureSpec, day: date) -> Path:
        if type(day) is not date:
            raise ValueError("day must be a date, not a datetime")
        return self._root / spec.feature_id / day.isoformat() / "values.parquet"

    def write_batch(self, spec: FeatureSpec, day: date, rows: Iterable[FeatureValue]) -> Path:
        """Validate every row against `spec.dtype` and the `day` partition,
        then publish -- either all rows land or none do (a `ValueError`
        raised mid-validation leaves no partial file on disk, since the
        Arrow table is only built, and the atomic hard link only attempted,
        after the full row list has been checked)."""
        path = self._path(spec, day)
        entity_ids: list[str] = []
        as_ofs: list[str] = []
        values: list[str] = []
        for row in rows:
            if row.as_of.tzinfo is None:
                raise ValueError("FeatureValue.as_of must be timezone-aware (CLAUDE.md §3)")
            if row.as_of.astimezone(timezone.utc).date() != day:
                raise ValueError("FeatureValue.as_of does not fall on the partition day")
            if not row.entity_id.strip():
                raise ValueError("entity_id must not be empty")
            validate_feature_value(spec, row.value)
            entity_ids.append(row.entity_id)
            as_ofs.append(row.as_of.astimezone(timezone.utc).isoformat())
            values.append(row.value)

        table = pa.table(
            {"entity_id": entity_ids, "as_of": as_ofs, "value": values}, schema=_SCHEMA
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
        try:
            pq.write_table(table, temporary)
            try:
                os.link(temporary, path)
            except FileExistsError:
                if _digest(temporary) != _digest(path):
                    raise FileExistsError(
                        "immutable feature partition has conflicting content"
                    ) from None
            return path
        finally:
            temporary.unlink(missing_ok=True)

    def read_batch(self, spec: FeatureSpec, day: date) -> Iterator[FeatureValue]:
        path = self._path(spec, day)
        with pq.ParquetFile(path) as parquet:
            if not parquet.schema_arrow.equals(_SCHEMA):
                raise ValueError("invalid feature parquet schema")
            for batch in parquet.iter_batches():
                columns = batch.to_pydict()
                for entity_id, as_of_iso, value in zip(
                    columns["entity_id"], columns["as_of"], columns["value"], strict=True
                ):
                    yield FeatureValue(
                        entity_id=entity_id,
                        as_of=datetime.fromisoformat(as_of_iso),
                        value=value,
                    )
