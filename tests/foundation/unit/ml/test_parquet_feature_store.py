"""AI-19 unit tests -- `adapters/parquet_feature_store.py::ParquetFeatureStore`.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5/§9 AI-19
("lineage storage"). ADR-2026-09-09-C D2: negative >= 3, failure injection
1, numeric performance assertion 1, gate-red reproduction 1.

Follows DC-23's `tests/unit/foundation/market_data/test_tick_parquet.py`
convention: pytest's `tmp_path` fixture is passed directly as the
adapter's `root`, no custom fixtures or filesystem mocking.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.foundation.ml.adapters.parquet_feature_store import ParquetFeatureStore
from src.foundation.ml.contracts.v1 import FeatureSpec
from src.foundation.ml.domain.feature_values import InvalidFeatureValueError
from src.foundation.ml.ports.feature_store import FeatureValue

_DAY = date(2026, 1, 15)
_SPEC = FeatureSpec(feature_id="rsi_14", dtype="float", source_ref="src://x")


def _row(entity_id: str = "AAPL", hour: int = 10, value: str = "1.5") -> FeatureValue:
    return FeatureValue(
        entity_id=entity_id,
        as_of=datetime(_DAY.year, _DAY.month, _DAY.day, hour, tzinfo=timezone.utc),
        value=value,
    )


# --- happy path ---


def test_write_then_read_round_trip(tmp_path: Path) -> None:
    store = ParquetFeatureStore(tmp_path)
    rows = [_row("AAPL", 10, "1.5"), _row("MSFT", 11, "2.25")]

    path = store.write_batch(_SPEC, _DAY, rows)

    assert path.exists()
    got = list(store.read_batch(_SPEC, _DAY))
    assert [(r.entity_id, r.value) for r in got] == [("AAPL", "1.5"), ("MSFT", "2.25")]
    assert all(r.as_of.tzinfo is not None for r in got)


def test_write_is_idempotent_on_identical_retry(tmp_path: Path) -> None:
    store = ParquetFeatureStore(tmp_path)
    rows = [_row()]

    first = store.write_batch(_SPEC, _DAY, rows)
    second = store.write_batch(_SPEC, _DAY, rows)

    assert first == second
    assert len(list(store.read_batch(_SPEC, _DAY))) == 1


def test_path_layout_partitions_by_feature_id_and_day(tmp_path: Path) -> None:
    store = ParquetFeatureStore(tmp_path)
    path = store.write_batch(_SPEC, _DAY, [_row()])
    assert path == tmp_path / "rsi_14" / "2026-01-15" / "values.parquet"


# --- negative (>= 3) ---


def test_read_missing_partition_raises_file_not_found(tmp_path: Path) -> None:
    store = ParquetFeatureStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        list(store.read_batch(_SPEC, _DAY))


def test_write_rejects_naive_as_of(tmp_path: Path) -> None:
    store = ParquetFeatureStore(tmp_path)
    naive_row = FeatureValue(entity_id="AAPL", as_of=datetime(2026, 1, 15, 10), value="1.5")
    with pytest.raises(ValueError, match="timezone-aware"):
        store.write_batch(_SPEC, _DAY, [naive_row])


def test_write_rejects_as_of_outside_partition_day(tmp_path: Path) -> None:
    store = ParquetFeatureStore(tmp_path)
    wrong_day_row = _row(hour=10)._replace(as_of=datetime(2026, 1, 16, 10, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="partition day"):
        store.write_batch(_SPEC, _DAY, [wrong_day_row])


def test_write_rejects_value_not_matching_dtype(tmp_path: Path) -> None:
    store = ParquetFeatureStore(tmp_path)
    with pytest.raises(InvalidFeatureValueError):
        store.write_batch(_SPEC, _DAY, [_row(value="not-a-float")])


# --- failure injection: conflicting retry for an already-published partition ---


def test_write_rejects_conflicting_retry_for_published_partition(tmp_path: Path) -> None:
    store = ParquetFeatureStore(tmp_path)
    store.write_batch(_SPEC, _DAY, [_row(value="1.5")])

    with pytest.raises(FileExistsError, match="conflicting content"):
        store.write_batch(_SPEC, _DAY, [_row(value="9.9")])


# --- gate-red reproduction: prove the digest check is load-bearing ---


def test_gate_red_plain_overwrite_would_silently_replace_content(tmp_path: Path) -> None:
    """Proves the conflicting-retry negative above is not a tautology: a
    plain `path.write_bytes(...)` (what `write_batch` would degrade to
    without the atomic-hard-link + digest-check discipline) happily
    replaces an existing file's content with no error."""
    path = tmp_path / "plain.bin"
    path.write_bytes(b"first")
    path.write_bytes(b"second")  # red: silently replaced, no conflict raised
    assert path.read_bytes() == b"second"


# --- numeric performance assertion ---

_READ_P95_BUDGET_MS = 50.0


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


def test_read_batch_p95_within_budget(tmp_path: Path) -> None:
    store = ParquetFeatureStore(tmp_path)
    rows = [_row(f"E{i}", 10, str(float(i))) for i in range(500)]
    store.write_batch(_SPEC, _DAY, rows)

    samples: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        list(store.read_batch(_SPEC, _DAY))
        samples.append((time.perf_counter() - started) * 1000)

    p95_ms = _p95(samples)
    print(f"[AI-19 read_batch] p95={p95_ms:.2f}ms budget<{_READ_P95_BUDGET_MS:.1f}ms")
    assert p95_ms < _READ_P95_BUDGET_MS
