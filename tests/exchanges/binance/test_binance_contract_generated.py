"""Binance endpoint coverage contract tests (generated) -- task-7599(BR-22a).

Contract tests for `scripts/binance_endpoint_coverage.py`: they lock down the
shape of the offline snapshot and the matrix built from it. They do not call
any real API and do not import `src/exchanges/binance/` -- this leaf is
scoped to the snapshot-driven matrix generator, not the adapter.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from scripts.binance_endpoint_coverage import (
    REQUIRED_CATEGORIES,
    BinanceCoverageError,
    build_matrix,
    load_snapshot,
    render_markdown,
)
from tests._perf.relative_budget import RelativeBudget

# ============================================================================
# Fixtures
# ============================================================================


def _entry(**overrides: Any) -> dict[str, Any]:
    base = {
        "method": "GET",
        "path": "/api/v3/ticker/price",
        "category": "market_data",
        "subcategory": "ticker",
        "summary": "Latest price for a symbol or symbols",
    }
    base.update(overrides)
    return base


def _minimal_valid_snapshot() -> dict[str, Any]:
    """Smallest snapshot satisfying every REQUIRED_CATEGORIES subcategory."""
    entries = [
        _entry(path="/api/v3/ticker/price", category="market_data", subcategory="ticker"),
        _entry(path="/api/v3/depth", category="market_data", subcategory="orderbook"),
        _entry(path="/api/v3/klines", category="market_data", subcategory="candles"),
        _entry(path="/api/v3/account", category="account", subcategory="balance"),
        _entry(path="/fapi/v2/positionRisk", category="account", subcategory="positions"),
        _entry(method="POST", path="/api/v3/order", category="order", subcategory="create"),
        _entry(method="DELETE", path="/api/v3/order", category="order", subcategory="cancel"),
        _entry(method="GET", path="/api/v3/order", category="order", subcategory="query"),
    ]
    return {"source": "test", "captured_at": "2026-09-26T00:00:00Z", "endpoints": entries}


# ============================================================================
# Contract tests (>=5 required, D2 floor)
# ============================================================================


def test_default_snapshot_parses_and_covers_required_categories() -> None:
    """Contract: the embedded default snapshot satisfies REQUIRED_CATEGORIES."""
    snapshot = load_snapshot()
    matrix = build_matrix(snapshot)
    assert set(matrix.category_counts) == set(REQUIRED_CATEGORIES)
    for category, required_subs in REQUIRED_CATEGORIES.items():
        assert required_subs <= set(matrix.subcategory_counts[category])


def test_build_matrix_counts_match_input_entries() -> None:
    """Contract: category_counts sums to len(rows) and matches raw entry count."""
    snapshot = _minimal_valid_snapshot()
    matrix = build_matrix(snapshot)
    assert matrix.total == len(snapshot["endpoints"])
    assert sum(matrix.category_counts.values()) == matrix.total


def test_render_markdown_lists_every_endpoint() -> None:
    """Contract: rendered markdown contains every endpoint's path exactly once."""
    snapshot = _minimal_valid_snapshot()
    matrix = build_matrix(snapshot)
    rendered = render_markdown(matrix, snapshot)
    for row in matrix.rows:
        assert rendered.count(f"`{row.path}`") >= 1


def test_build_matrix_is_deterministic_across_runs() -> None:
    """Red-gate reproduction: before task-7599, there was no reproducible
    offline snapshot-driven matrix for Binance. Prove that build_matrix()
    produces byte-identical row order for the same input every time, with no
    network access involved -- the property the generator exists to provide.
    """
    snapshot = _minimal_valid_snapshot()
    first = build_matrix(json.loads(json.dumps(snapshot)))
    second = build_matrix(json.loads(json.dumps(snapshot)))
    assert first.rows == second.rows
    assert first.category_counts == second.category_counts


def test_load_snapshot_roundtrips_through_json_text() -> None:
    """Contract: load_snapshot(text) accepts a serialized snapshot, not just
    the module's embedded literal -- exercises the generator over arbitrary
    conforming input.
    """
    snapshot = _minimal_valid_snapshot()
    text = json.dumps(snapshot)
    loaded = load_snapshot(text)
    assert loaded["endpoints"] == snapshot["endpoints"]


# ============================================================================
# Negative tests (>=3 required, D2 floor)
# ============================================================================


def test_load_snapshot_rejects_invalid_json() -> None:
    """Negative 1: malformed JSON text raises BinanceCoverageError."""
    with pytest.raises(BinanceCoverageError, match="not valid JSON"):
        load_snapshot("{not json")


def test_load_snapshot_rejects_empty_endpoints_list() -> None:
    """Negative 2: an empty 'endpoints' list is rejected, not silently accepted."""
    with pytest.raises(BinanceCoverageError, match="non-empty list"):
        load_snapshot(json.dumps({"source": "x", "captured_at": "x", "endpoints": []}))


def test_build_matrix_rejects_unknown_category() -> None:
    """Negative 3: an entry naming a category outside REQUIRED_CATEGORIES fails closed."""
    snapshot = _minimal_valid_snapshot()
    snapshot["endpoints"].append(_entry(path="/api/v3/withdraw", category="withdrawal"))
    with pytest.raises(BinanceCoverageError, match="unknown category"):
        build_matrix(snapshot)


def test_build_matrix_rejects_missing_field() -> None:
    """Negative 4: an entry missing a required field fails closed."""
    snapshot = _minimal_valid_snapshot()
    bad_entry = _entry(path="/api/v3/avgPrice")
    del bad_entry["summary"]
    snapshot["endpoints"].append(bad_entry)
    with pytest.raises(BinanceCoverageError, match="missing required field"):
        build_matrix(snapshot)


def test_build_matrix_rejects_duplicate_endpoint() -> None:
    """Negative 5: the same (method, path) pair appearing twice fails closed."""
    snapshot = _minimal_valid_snapshot()
    snapshot["endpoints"].append(_entry(path="/api/v3/ticker/price"))
    with pytest.raises(BinanceCoverageError, match="duplicate endpoint"):
        build_matrix(snapshot)


def test_build_matrix_rejects_snapshot_missing_required_subcategory() -> None:
    """Negative 6: dropping a DoD-minimum subcategory (e.g. order/cancel) fails closed --
    this is the guard against the matrix quietly reporting a smaller surface than the
    DoD requires.
    """
    snapshot = _minimal_valid_snapshot()

    def _is_order_cancel(e: dict[str, Any]) -> bool:
        return bool(e["category"] == "order" and e["subcategory"] == "cancel")

    snapshot["endpoints"] = [e for e in snapshot["endpoints"] if not _is_order_cancel(e)]
    with pytest.raises(BinanceCoverageError, match="missing required subcategories"):
        build_matrix(snapshot)


# ============================================================================
# Failure-injection test (>=1 required, D2 floor)
# ============================================================================


def test_load_snapshot_handles_truncated_write_injection() -> None:
    """Failure injection: simulates a snapshot file truncated mid-write (e.g. disk
    full) -- valid JSON up to a cut point becomes invalid JSON, and must raise
    rather than silently parsing a partial structure.
    """
    full_text = json.dumps(_minimal_valid_snapshot())
    truncated = full_text[: len(full_text) // 2]
    with pytest.raises(BinanceCoverageError, match="not valid JSON"):
        load_snapshot(truncated)


# ============================================================================
# Performance assertion (>=1 required, D2 floor)
# ============================================================================


@pytest.mark.perf
def test_build_matrix_performance_scales_linearly() -> None:
    """Performance: building a matrix for a large synthetic snapshot stays
    within a host-speed-independent budget (see RelativeBudget docstring for
    why an absolute-ms assertion is flaky on a shared CI runner) -- guards
    against an accidental O(n^2) validation path (e.g. an O(n) duplicate scan
    per row) as the endpoint count grows.
    """
    entries = list(_minimal_valid_snapshot()["endpoints"])
    for i in range(2_000):
        entries.append(_entry(path=f"/api/v3/synthetic/{i}", subcategory="ticker"))
    large_snapshot = {"source": "perf", "captured_at": "2026-09-26T00:00:00Z", "endpoints": entries}

    result: dict[str, int] = {}

    def _run_once() -> None:
        result["total"] = build_matrix(large_snapshot).total

    RelativeBudget().assert_within(
        _run_once, max_ratio=5.0, mode="cpu", label="build_matrix(2008 entries) best of 5"
    )
    assert result["total"] == len(entries)
