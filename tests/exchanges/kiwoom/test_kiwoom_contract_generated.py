"""task-7604(BR-23a) -- Kiwoom endpoint coverage matrix contract tests (generated).

Contract tests generated from `scripts/kiwoom_endpoint_coverage.py`'s
checked-in endpoint snapshot -- one parametrized case per endpoint plus a
handful of matrix-level checks. No network calls, no real keys, no DB.

D2 floor: negative tests >= 3 (see the "Negative tests" section), one
failure-injection test (`test_main_reports_failure_on_malformed_snapshot`),
one numeric assertion cross-checked by hand
(`test_coverage_percent_matches_manual_calculation`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from scripts.kiwoom_endpoint_coverage import (
    _VALID_CATEGORIES,
    KiwoomCoverageError,
    build_matrix,
    classify,
    load_reference,
    main,
    render_markdown,
    scan_adapter_source,
)

_API_ID_RE = re.compile(r"^(ka|kt)\d{5}$")
_PATH_PREFIX = "/api/dostk/"


@pytest.fixture
def reference() -> dict[str, Any]:
    return load_reference()


@pytest.fixture
def adapter_source() -> str:
    return scan_adapter_source()


# ---------------------------------------------------------------------------
# Generated contract tests: one case per endpoint in the snapshot.
# ---------------------------------------------------------------------------


def _endpoint_ids(reference: dict[str, Any]) -> list[str]:
    return [ep["api_id"] for ep in reference["endpoints"]]


_REFERENCE = load_reference()
_ENDPOINTS = _REFERENCE["endpoints"]


@pytest.mark.parametrize("endpoint", _ENDPOINTS, ids=_endpoint_ids(_REFERENCE))
def test_endpoint_contract_shape(endpoint: dict[str, Any]) -> None:
    """Contract: every snapshot endpoint follows the vendor's REST convention."""
    assert endpoint["path"].startswith(_PATH_PREFIX)
    assert endpoint["method"] == "POST"
    assert _API_ID_RE.match(endpoint["api_id"]), endpoint["api_id"]
    assert endpoint["category"] in _VALID_CATEGORIES
    assert endpoint["summary"]


# ---------------------------------------------------------------------------
# Matrix-level contract tests.
# ---------------------------------------------------------------------------


def test_snapshot_has_minimum_required_categories(reference: dict[str, Any]) -> None:
    """DoD minimum: quotes, account_balance, and order (new/cancel/amend)."""
    categories = {ep["category"] for ep in reference["endpoints"]}
    assert {"quotes", "account_balance", "order_new", "order_cancel", "order_amend"} <= categories


def test_build_matrix_marks_kt_endpoints_implemented(
    reference: dict[str, Any], adapter_source: str
) -> None:
    matrix = build_matrix(reference, adapter_source)
    by_id = {row.api_id: row.reason for row in matrix.rows}
    for kt_id in ("kt00001", "kt00018", "kt00009", "kt10000", "kt10001", "kt10002", "kt10003"):
        assert by_id[kt_id] == "implemented", kt_id


def test_build_matrix_marks_quote_endpoints_not_started(
    reference: dict[str, Any], adapter_source: str
) -> None:
    """No REST market-data mixin exists on main yet -- ka-prefixed IDs stay open."""
    matrix = build_matrix(reference, adapter_source)
    by_id = {row.api_id: row.reason for row in matrix.rows}
    for ka_id in ("ka10001", "ka10004", "ka10080"):
        assert by_id[ka_id] == "not_started", ka_id


def test_coverage_percent_matches_manual_calculation(
    reference: dict[str, Any], adapter_source: str
) -> None:
    matrix = build_matrix(reference, adapter_source)
    assert matrix.total == 10
    assert matrix.implemented == 7
    assert matrix.percent == pytest.approx(70.0)


def test_render_markdown_reports_category_counts(
    reference: dict[str, Any], adapter_source: str
) -> None:
    matrix = build_matrix(reference, adapter_source)
    markdown = render_markdown(reference, matrix)
    assert "| `quotes` | 3 |" in markdown
    assert "| `account_balance` | 3 |" in markdown
    assert "70.00%" in markdown


def test_main_writes_matrix_file(tmp_path: Path) -> None:
    """Failure-injection counterpart's happy path: main() writes the doc as configured."""
    out = tmp_path / "KIWOOM_COVERAGE.md"
    exit_code = main(["--matrix-md", str(out)])
    assert exit_code == 0
    assert out.exists()
    assert "Kiwoom Securities Endpoint Coverage Matrix" in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Negative tests (>= 3 required).
# ---------------------------------------------------------------------------


def test_load_reference_rejects_empty_endpoints() -> None:
    with pytest.raises(KiwoomCoverageError, match="no endpoints"):
        load_reference(json.dumps({"source_url": "x", "endpoints": []}))


def test_load_reference_rejects_unknown_category() -> None:
    bad = {
        "source_url": "x",
        "endpoints": [
            {"path": "/api/dostk/acnt", "method": "POST", "api_id": "kt99999", "category": "bogus"}
        ],
    }
    with pytest.raises(KiwoomCoverageError, match="unknown category"):
        load_reference(json.dumps(bad))


def test_scan_adapter_source_rejects_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(KiwoomCoverageError, match="not found"):
        scan_adapter_source(tmp_path / "does_not_exist")


def test_classify_defaults_unknown_endpoint_to_not_started() -> None:
    assert classify("kt00099", implemented=False) == "not_started"


def test_main_reports_failure_on_malformed_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Failure-injection: a corrupted snapshot must fail closed, not silently pass."""
    import scripts.kiwoom_endpoint_coverage as module

    monkeypatch.setattr(module, "_SNAPSHOT_JSON", json.dumps({"source_url": "x", "endpoints": []}))
    exit_code = module.main(["--matrix-md", str(tmp_path / "out.md")])
    assert exit_code == 1
