"""DC-23: bounded-batch tick/quote Parquet archive (pyarrow, Apache-2.0).

Layout: root/venue/instrument_id/YYYY-MM-DD/{trades,quotes}.parquet.
Decimal and nanosecond timestamps retain their exact string representations.
Reads yield Arrow RecordBatches directly, without constructing row DTOs.
Writes publish complete immutable partitions using an atomic hard link: identical
retries succeed, conflicting content fails closed (105 idempotent writes).
Missing partitions and corrupt files raise; hot snapshot metadata is unsupported.
"""
from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Iterator
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import TypeAdapter

from src.foundation.market_data.adapters.storage.warm_parquet import AsOfNotSupportedError
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2.instruments import ULID
from src.foundation.market_data.contracts.v2.microstructure import QuoteL1, TradeTick

Kind = Literal["trades", "quotes"]
_MODELS: dict[str, type[TradeTick] | type[QuoteL1]] = {"trades": TradeTick, "quotes": QuoteL1}
_DAY_NS = 86_400_000_000_000
_EPOCH = date(1970, 1, 1)


def _schema(kind: Kind) -> pa.Schema:
    return pa.schema([(name, pa.string()) for name in _MODELS[kind].model_fields])


def _digest(path: Path) -> bytes:
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.digest()


class TickParquetStorage:
    """Immutable daily partitions; memory scales with batch_size, not day size.

    Consumers convert selected Arrow string columns to Decimal/int as needed.
    Input order, including duplicates, is preserved; aggregation owns deduplication.
    """

    def __init__(self, root: Path, *, batch_size: int = 8192) -> None:
        if not 1 <= batch_size <= 65536:
            raise ValueError("batch_size must be between 1 and 65536")
        self._root = Path(root)
        self._batch_size = batch_size

    def _path(self, venue: Venue, instrument_id: str, day: date, kind: Kind) -> Path:
        venue = Venue(venue)
        instrument_id = TypeAdapter(ULID).validate_python(instrument_id)
        if type(day) is not date or kind not in _MODELS:
            raise ValueError("expected a date and trades/quotes kind")
        return self._root / venue.value / instrument_id / day.isoformat() / f"{kind}.parquet"

    def write_day(
        self, venue: Venue, instrument_id: str, day: date,
        records: Iterable[TradeTick] | Iterable[QuoteL1], *, kind: Kind,
        as_of: datetime | None = None,
    ) -> Path:
        """Stream existing DC-19 DTOs; publish only after every record validates."""
        if as_of is not None:
            raise AsOfNotSupportedError("tick parquet does not support as_of")
        path = self._path(venue, instrument_id, day, kind)
        schema = _schema(kind)
        names = schema.names
        model = _MODELS[kind]
        day_number = (day - _EPOCH).days
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
        try:
            with pq.ParquetWriter(temporary, schema) as writer:
                columns: dict[str, list[str]] = {name: [] for name in names}
                for record in records:
                    if not isinstance(record, model):
                        raise ValueError("record type does not match partition kind")
                    # Revalidate even frozen DTOs: model_copy/model_construct can
                    # bypass validation. The field mapping avoids two complete
                    # serialization passes before Arrow receives string columns.
                    record = model.model_validate(vars(record))
                    if (record.venue != venue or record.instrument_id != instrument_id
                            or record.ts_event // _DAY_NS != day_number):
                        raise ValueError("record does not match UTC day/venue/instrument partition")
                    values = vars(record)
                    for name in names:
                        value = values[name]
                        columns[name].append(str(value.value if isinstance(value, Enum) else value))
                    if len(columns["seq"]) == self._batch_size:
                        writer.write_table(pa.table(columns, schema=schema))
                        columns = {name: [] for name in names}
                if columns["seq"]:
                    writer.write_table(pa.table(columns, schema=schema))
            try:
                os.link(temporary, path)
            except FileExistsError:
                if _digest(temporary) != _digest(path):
                    raise FileExistsError("immutable partition has conflicting content") from None
            return path
        finally:
            temporary.unlink(missing_ok=True)

    def read_columns(
        self, venue: Venue, instrument_id: str, day: date, *, kind: Kind,
        columns: list[str] | None = None, as_of: datetime | None = None,
    ) -> Iterator[pa.RecordBatch]:
        """Project columns directly from Parquet in bounded Arrow batches.

        A missing partition raises FileNotFoundError, distinct from a stored empty
        day. Errors are checked immediately, including as_of before any file I/O.
        """
        if as_of is not None:
            raise AsOfNotSupportedError("tick parquet does not support as_of")
        path = self._path(venue, instrument_id, day, kind)
        schema = _schema(kind)
        if columns is not None and (not columns or len(set(columns)) != len(columns)
                                    or any(name not in schema.names for name in columns)):
            raise ValueError("projection must contain unique known columns")
        return self._batches(path, schema, columns)

    def _batches(
        self, path: Path, schema: pa.Schema, columns: list[str] | None,
    ) -> Iterator[pa.RecordBatch]:
        with pq.ParquetFile(path) as parquet:
            if not parquet.schema_arrow.equals(schema):
                raise ValueError("invalid microstructure parquet schema")
            yield from parquet.iter_batches(batch_size=self._batch_size, columns=columns)
