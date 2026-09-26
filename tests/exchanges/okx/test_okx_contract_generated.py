"""OKX endpoint coverage matrix -- contract tests (generated) -- task-7594(BR-21a).

Exercises `scripts/okx_endpoint_coverage.py` against its embedded reference snapshot
and against the real `src/exchanges/okx/trading_mixin.py` adapter source. Does not hit
any network -- purely static/offline, matching the module under test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts import okx_endpoint_coverage as okx_coverage
from tests._perf.relative_budget import RelativeBudget

# ============================================================================
# Contract tests: reference schema (>=5 required by task-7594 DoD)
# ============================================================================


def test_default_reference_has_minimum_categories() -> None:
    """Contract: the embedded snapshot covers all three DoD-mandated categories."""
    reference = okx_coverage.load_reference(None)
    categories = {e["category"] for e in reference["endpoints"]}
    assert categories == {"market_data", "account", "order"}


def test_default_reference_endpoints_all_pass_validation() -> None:
    """Contract: every embedded endpoint entry satisfies the schema contract
    (load_reference() itself calls _validate_entry on each one; this asserts
    it didn't silently skip any)."""
    reference = okx_coverage.load_reference(None)
    assert len(reference["endpoints"]) >= 10
    for entry in reference["endpoints"]:
        # Re-validating here (not just relying on load_reference not raising)
        # pins the return shape callers depend on.
        path, method, category, summary = okx_coverage._validate_entry(entry)
        assert path.startswith("/api/v5/")
        assert method in okx_coverage._VALID_METHODS
        assert category in okx_coverage._VALID_CATEGORIES
        assert summary


def test_extract_implemented_pairs_matches_real_adapter() -> None:
    """Contract: the place/cancel/amend trio in trading_mixin.py (task BR-21d,
    already on main) is discovered via the real (method, path) call sites."""
    pairs = okx_coverage.extract_implemented_pairs(okx_coverage.DEFAULT_ADAPTER_DIR)
    assert ("POST", "/api/v5/trade/order") in pairs
    assert ("POST", "/api/v5/trade/cancel-order") in pairs
    assert ("POST", "/api/v5/trade/amend-order") in pairs


def test_get_order_query_not_marked_implemented() -> None:
    """Contract (this is the gate this leaf closes): `GET /api/v5/trade/order`
    (order lookup) shares its path with the implemented `POST` place-order
    endpoint but must not be reported as implemented -- no query/lookup
    endpoint exists yet on `main`."""
    pairs = okx_coverage.extract_implemented_pairs(okx_coverage.DEFAULT_ADAPTER_DIR)
    assert ("GET", "/api/v5/trade/order") not in pairs


def test_build_matrix_classifies_known_endpoints() -> None:
    """Contract: build_matrix() end-to-end against the real reference + real
    adapter source classifies each endpoint correctly."""
    reference = okx_coverage.load_reference(None)
    pairs = okx_coverage.extract_implemented_pairs(okx_coverage.DEFAULT_ADAPTER_DIR)
    matrix = okx_coverage.build_matrix(reference, pairs)

    by_key = {(row.method, row.path): row.reason for row in matrix.rows}
    assert by_key[("POST", "/api/v5/trade/order")] == "implemented"
    assert by_key[("GET", "/api/v5/trade/order")] == "not_started"
    assert by_key[("GET", "/api/v5/market/ticker")] == "not_started"
    assert matrix.implemented == 3
    assert matrix.total == len(reference["endpoints"])


def test_render_markdown_contains_expected_sections() -> None:
    """Contract: the generated markdown has the DoD-required category table
    plus the summary/full-matrix sections, and its counts are consistent."""
    reference = okx_coverage.load_reference(None)
    pairs = okx_coverage.extract_implemented_pairs(okx_coverage.DEFAULT_ADAPTER_DIR)
    matrix = okx_coverage.build_matrix(reference, pairs)
    doc = okx_coverage.render_markdown(reference, matrix)

    assert "## Summary" in doc
    assert "## By category" in doc
    assert "## Full endpoint matrix" in doc
    assert f"| {matrix.implemented} | " in doc
    assert "POST" in doc and "/api/v5/trade/order" in doc


def test_classify_out_of_scope_via_scope_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contract: an ADR-recorded scope exclusion classifies as out_of_scope,
    not not_started, when neither implemented nor a network fetch applies."""
    monkeypatch.setitem(
        okx_coverage._SCOPE_OVERRIDES, "GET /api/v5/account/config", "excluded by ADR (example)"
    )
    reason = okx_coverage.classify("GET", "/api/v5/account/config", set())
    assert reason == "out_of_scope"


def test_no_network_calls_in_coverage_script() -> None:
    """Contract (DoD item 2): the script makes zero network calls -- statically
    verified by asserting it imports none of the network-capable stdlib
    modules, rather than trusting a docstring claim."""
    source = Path(okx_coverage.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint({"urllib", "socket", "http", "requests", "httpx", "aiohttp"})


# ============================================================================
# Red-gate reproduction
# ============================================================================


def test_naive_substring_matching_would_falsely_report_implemented_red_gate() -> None:
    """Red-gate reproduction: the sibling scripts (bitget/nh/upbit coverage)
    classify by path-substring alone, ignoring HTTP method. Reproduce that
    naive approach directly against the real adapter source to show it would
    misreport the unimplemented `GET /api/v5/trade/order` as done -- exactly
    the gate `extract_implemented_pairs()`'s method-aware matching closes."""
    adapter_source = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(okx_coverage.DEFAULT_ADAPTER_DIR.rglob("*.py"))
    )
    naive_would_report_implemented = "/api/v5/trade/order" in adapter_source
    assert naive_would_report_implemented is True

    precise_pairs = okx_coverage.extract_implemented_pairs(okx_coverage.DEFAULT_ADAPTER_DIR)
    assert ("GET", "/api/v5/trade/order") not in precise_pairs


# ============================================================================
# Negative tests (>=3 required): missing schema field / type mismatch rejected
# ============================================================================


def test_validate_entry_missing_path_field_rejected() -> None:
    entry = {"method": "GET", "category": "market_data", "summary": "x"}
    with pytest.raises(okx_coverage.OkxCoverageError, match="path"):
        okx_coverage._validate_entry(entry)


def test_validate_entry_method_type_mismatch_rejected() -> None:
    """Negative: method as int (not str) is rejected, not coerced."""
    entry = {
        "path": "/api/v5/market/ticker",
        "method": 200,
        "category": "market_data",
        "summary": "x",
    }
    with pytest.raises(okx_coverage.OkxCoverageError, match="method"):
        okx_coverage._validate_entry(entry)


def test_validate_entry_invalid_category_rejected() -> None:
    entry = {
        "path": "/api/v5/market/ticker",
        "method": "GET",
        "category": "not_a_real_category",
        "summary": "x",
    }
    with pytest.raises(okx_coverage.OkxCoverageError, match="category"):
        okx_coverage._validate_entry(entry)


def test_validate_entry_path_missing_api_v5_prefix_rejected() -> None:
    entry = {"path": "/v5/trade/order", "method": "POST", "category": "order", "summary": "x"}
    with pytest.raises(okx_coverage.OkxCoverageError, match="path"):
        okx_coverage._validate_entry(entry)


def test_load_reference_empty_endpoints_rejected(tmp_path: Path) -> None:
    """Negative: a reference file with no endpoints is rejected, not silently
    treated as 0% coverage."""
    reference_file = tmp_path / "empty.json"
    reference_file.write_text('{"endpoints": []}', encoding="utf-8")
    with pytest.raises(okx_coverage.OkxCoverageError, match="no endpoints"):
        okx_coverage.load_reference(reference_file)


def test_load_reference_from_file_with_malformed_entry_rejected(tmp_path: Path) -> None:
    """Negative: a reference file whose entry is missing a required field is
    rejected at load time, not deferred until matrix rendering."""
    reference_file = tmp_path / "bad.json"
    reference_file.write_text(
        '{"endpoints": [{"path": "/api/v5/market/ticker", "method": "GET"}]}',
        encoding="utf-8",
    )
    with pytest.raises(okx_coverage.OkxCoverageError):
        okx_coverage.load_reference(reference_file)


# ============================================================================
# Failure-injection test (>=1 required per D2)
# ============================================================================


def test_pairs_from_file_syntax_error_returns_empty_set_not_raise() -> None:
    """Failure-injection: a corrupted/unparseable .py file must not crash the
    whole matrix build -- degrade to an empty pair set for that file."""
    corrupted_source = "def broken(:\n    this is not valid python !!!"
    assert okx_coverage._pairs_from_file(corrupted_source) == set()


# ============================================================================
# Edge cases / detection mechanics
# ============================================================================


def test_pairs_from_file_resolves_named_constant() -> None:
    """A call site passing a named module-level constant (the real
    trading_mixin.py convention) resolves to its string value, not just an
    inline literal."""
    source = (
        '_MY_PATH = "/api/v5/example/path"\n'
        "class C:\n"
        "    async def f(self):\n"
        '        await self._request("POST", _MY_PATH, body={})\n'
    )
    assert okx_coverage._pairs_from_file(source) == {("POST", "/api/v5/example/path")}


def test_extract_implemented_pairs_missing_dir_returns_empty(tmp_path: Path) -> None:
    """A missing adapter directory is a valid state (no market-data/account
    adapter yet), same tolerance as `upbit_openapi_coverage.py`."""
    missing = tmp_path / "does_not_exist"
    assert okx_coverage.extract_implemented_pairs(missing) == set()


# ============================================================================
# Performance assertion (>=1 numeric, required per D2)
# ============================================================================


@pytest.mark.perf
def test_build_matrix_performance() -> None:
    """Numeric performance assertion: building the full matrix (13 reference
    endpoints x adapter scan) 50 times should stay cheap relative to a
    same-process calibration loop (see tests/_perf/relative_budget.py)."""
    reference = okx_coverage.load_reference(None)
    pairs = okx_coverage.extract_implemented_pairs(okx_coverage.DEFAULT_ADAPTER_DIR)

    def _run_fifty() -> None:
        for _ in range(50):
            matrix = okx_coverage.build_matrix(reference, pairs)
            assert matrix.total == len(reference["endpoints"])

    RelativeBudget().assert_within(
        _run_fifty, max_ratio=2.0, mode="cpu", label="build_matrix x50"
    )
