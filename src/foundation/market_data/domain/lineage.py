"""LA-8/LA-23b — Batch lineage hash. Digest for §A4 (doc §31) audit event binding.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-8, §9.2 LA-8,
docs/design/ADR-2026-09-04-A-market-data-replay-perf.md#2.

Follows the canonical JSON rules from
`src/foundation/ledger/domain/hash_chain.py` (sorted keys, UTF-8,
`default=str` to stringify Decimal, etc.). `batch_hash` must be order-
independent for the same reason as `hash_chain.lines_digest` — even if the
store reads records with a different sort order, the same batch must produce
the same hash so verification does not waver. No I/O — contains pure
functions only.

**ADR-2026-09-04-A #2 streaming re-implementation note**: The ADR text says
"fetching with ORDER BY loses the full sort order", but this function must
match the `batch_hash` value already stored at write time by
`ingest_candles`/`ingest_ticks`, byte-for-byte (P3 WORM, no recompute or
backfill of stored hashes — same ADR #2). The sort key is the record's own
canonical JSON string (primarily the first alphabetic field value; e.g.,
`CandleRecord` sorts on `close`), independent of the DB's `open_time ORDER
BY` — so this implementation keeps the sort but removes two things: (1) the
bulk consumer now reads via the column-oriented path
(`domain/candle_columns`), eliminating pydantic validation cost at record
creation itself, and (2) instead of joining sorted strings with
`"\\n".join()` at once and then hashing, we switched to incremental
`hashlib` streaming that avoids creating a large intermediate string.
sha256 (Merkle-Damgard construction) guarantees that a sequence of
`update()` calls produces byte-identical output to hashing the concatenated
whole at once — its identity with `_batch_hash_reference` (legacy impl,
preserved for comparison only) is proven by the property test in
`tests/unit/market_data/test_lineage.py` (200+ random batches).

**task-1136 (esc-ci-8e93e475afa9 QA) serialization-cost note**: In large
batches, the dominant cost of `batch_hash` is not sorting but creating the
per-record canonical JSON string (measured at 20k-100k records:
`model_dump(mode="json")` and `json.dumps` each take roughly half).
The only part we can reduce without changing the hash value is the
`JSONEncoder` creation and argument-checking overhead that `json.dumps`
constructs on every call. We reuse a single encoder at module level with
identical arguments (`sort_keys=True, default=str`, rest defaults) —
`json.dumps(obj, sort_keys=True, default=str)` is, per the stdlib
implementation, exactly equivalent to
`JSONEncoder(sort_keys=True, default=str).encode(obj)`, so output bytes
are identical (`_batch_hash_reference` keeps calling `json.dumps` directly
so it does not rely on this assumption, and the fixed-vector golden test
pins the digest value itself). Larger savings like `model_dump_json()` or
`TypeAdapter(list[T])` bulk serialization are not used without
`hash_version=2` (P3 WORM) because they either produce different bytes
(former) or may desync from `model_dump()` by serializing subclass
instances as declared types (latter).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

__all__ = ["batch_hash", "request_fingerprint"]

# Identical encoder to `json.dumps(value, sort_keys=True, default=str)`
# (module docstring, task-1136 note). Safe to reuse and call concurrently —
# it has no mutable state (markers for cycle detection are created fresh per
# encode call).
_CANONICAL_ENCODER = json.JSONEncoder(sort_keys=True, default=str)


def _canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return _CANONICAL_ENCODER.encode(value)


def _canonical_json_reference(value: Any) -> str:
    """Legacy implementation — do not delete. Baseline to verify that
    `_canonical_json` still produces byte-identical output to a direct
    `json.dumps` call after switching to the reusable encoder."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, default=str)


def _batch_hash_reference(records: Sequence[Any]) -> str:
    """Legacy implementation — do not delete. Baseline the property test
    compares against, to verify `batch_hash` still produces byte-identical
    output (module docstring). Serialisation, sorting, and aggregation all
    follow the old path (`_canonical_json_reference` + `"\\n".join`), so any
    step change in the new impl will be caught."""
    canonical_rows = sorted(_canonical_json_reference(record) for record in records)
    payload = "\n".join(canonical_rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def batch_hash(records: Sequence[Any]) -> str:
    """Digest of a record batch. Produces the same value regardless of input
    order, and is byte-identical to `_batch_hash_reference` (see module
    docstring).

    Serialises each record to a canonical JSON string with sorted keys,
    sorts those strings lexicographically, then feeds them one-by-one into
    `hashlib` with `\\n` delimiters (incremental streaming) instead of
    joining into a single string."""
    canonical_rows = sorted(_canonical_json(record) for record in records)
    hasher = hashlib.sha256()
    for index, row in enumerate(canonical_rows):
        if index:
            hasher.update(b"\n")
        hasher.update(row.encode("utf-8"))
    return hasher.hexdigest()


def request_fingerprint(source: str, params: Mapping[str, Any]) -> str:
    """요청 지문: source + 정렬된 파라미터의 canonical JSON sha256."""
    payload = _canonical_json({"source": source, "params": dict(params)})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
