"""OKX endpoint coverage matrix -- BR-21(task-7594), ADR-2026-09-26-A.

Same offline/deterministic pattern as `bitget_coverage.py`/`nh_openapi_coverage.py`/
`upbit_openapi_coverage.py` (`docs/exchanges/ADDING_AN_EXCHANGE.md` SS4): a fixed
reference list of endpoints is compared against adapter source and rendered into
`docs/design/OKX_COVERAGE.md`. Zero network calls -- `_ENDPOINTS` below is an embedded
snapshot curated from the public docs-v5 REST reference (this environment has no
outbound access to probe it live, and task-7594's file list has no companion fetch
script), in the same spirit as `upbit_openapi_fetch.py`'s `_CANDIDATE_ENDPOINTS`.
`load_reference()` accepts `--reference-file` for a future live-fetched snapshot.

Detection differs from the sibling scripts: they classify by path-substring match alone,
ignoring HTTP method. Unsafe here -- OKX reuses one path across methods (`POST
/api/v5/trade/order` to place, `GET /api/v5/trade/order` to fetch), and
`src/exchanges/okx/trading_mixin.py` (task BR-21d, already on `main` -- confirmed via
`git grep -r 'okx' src/exchanges`) implements exactly that place/cancel/amend trio, all
`POST`. Substring matching would misreport the unimplemented `GET .../order` lookup as
done. `extract_implemented_pairs()` instead resolves the (method, path) pair actually
passed to each `self._request(...)` call site via an AST walk.

Usage: `python scripts/okx_endpoint_coverage.py` (repo root). Exit 0 = matrix written,
1 = malformed reference/adapter input.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ADAPTER_DIR = ROOT / "src" / "exchanges" / "okx"
DEFAULT_MATRIX_MD = ROOT / "docs" / "design" / "OKX_COVERAGE.md"

Reason = Literal["implemented", "out_of_scope", "not_started"]
_VALID_REASONS = frozenset({"implemented", "out_of_scope", "not_started"})
_VALID_METHODS = frozenset({"GET", "POST", "PUT", "DELETE"})
_VALID_CATEGORIES = frozenset({"market_data", "account", "order"})

# Endpoints an ADR has explicitly excluded ("METHOD path" -> rationale). Empty today.
_SCOPE_OVERRIDES: dict[str, str] = {}

_EXTRACTION_METHOD = (
    "human-curated from the public docs-v5 REST reference (Market Data / Account / "
    "Trade sections); not live-probed -- no outbound network access in this environment"
)

# path, method, category, summary -- curated from https://www.okx.com/docs-v5/en/.
# Minimum categories per task-7594 DoD: market_data, account, order.
_ENDPOINTS: tuple[tuple[str, str, str, str], ...] = (
    ("/api/v5/market/ticker", "GET", "market_data", "Get ticker for a single instrument"),
    ("/api/v5/market/tickers", "GET", "market_data", "Get tickers for all instruments in a type"),
    ("/api/v5/market/books", "GET", "market_data", "Get order book depth"),
    ("/api/v5/market/candles", "GET", "market_data", "Get candlesticks"),
    ("/api/v5/market/history-candles", "GET", "market_data", "Get historical candlesticks"),
    ("/api/v5/account/balance", "GET", "account", "Get account asset balances"),
    ("/api/v5/account/positions", "GET", "account", "Get open positions"),
    ("/api/v5/account/config", "GET", "account", "Get account configuration"),
    ("/api/v5/trade/order", "POST", "order", "Place order"),
    ("/api/v5/trade/cancel-order", "POST", "order", "Cancel order"),
    ("/api/v5/trade/amend-order", "POST", "order", "Amend (modify) order"),
    ("/api/v5/trade/order", "GET", "order", "Get order details"),
    ("/api/v5/trade/orders-pending", "GET", "order", "Get a list of pending orders"),
)


def _default_reference() -> dict[str, Any]:
    endpoints = [
        {"path": path, "method": method, "category": category, "summary": summary}
        for path, method, category, summary in _ENDPOINTS
    ]
    return {
        "source_url": "https://www.okx.com/docs-v5/en/",
        "extraction_method": _EXTRACTION_METHOD,
        "fetched_at": "2026-09-26T00:00:00+00:00",
        "spec_version": "v5",
        "spec_title": "OKX API v5",
        "endpoints": endpoints,
    }


class OkxCoverageError(ValueError):
    """Malformed reference/adapter input, or a schema contract violation."""


@dataclass(frozen=True)
class EndpointRow:
    path: str
    method: str
    category: str
    summary: str
    reason: Reason


@dataclass(frozen=True)
class MatrixResult:
    rows: tuple[EndpointRow, ...]
    implemented: int
    total: int

    @property
    def percent(self) -> float:
        return round(self.implemented / self.total * 100, 2) if self.total else 0.0


def _validate_entry(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    """Enforce the endpoint-entry schema -- raises on the first violation
    instead of silently coercing a malformed entry (missing field, wrong
    type, or a value outside the allowed sets)."""
    fields: dict[str, str] = {}
    for name in ("path", "method", "category", "summary"):
        value = entry.get(name)
        if not isinstance(value, str) or not value:
            raise OkxCoverageError(f"missing/invalid string field {name!r}: {entry!r}")
        fields[name] = value
    path, method, category = fields["path"], fields["method"], fields["category"]
    if not path.startswith("/api/v5/"):
        raise OkxCoverageError(f"path must start with /api/v5/: {path!r}")
    if method not in _VALID_METHODS:
        raise OkxCoverageError(f"unsupported method {method!r} for {path!r}")
    if category not in _VALID_CATEGORIES:
        raise OkxCoverageError(f"unsupported category {category!r} for {path!r}")
    return path, method, category, fields["summary"]


def load_reference(path: Path | None) -> dict[str, Any]:
    data = _default_reference() if path is None or not path.exists() else json.loads(
        path.read_text(encoding="utf-8")
    )
    endpoints = data.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        raise OkxCoverageError(f"reference has no endpoints: {path or '<embedded>'}")
    for entry in endpoints:
        _validate_entry(entry)
    return data


def _pairs_from_file(source: str) -> set[tuple[str, str]]:
    """(method, path) pairs passed to `self._request(...)` call sites in one file.
    Resolves a named constant argument (e.g. `_ORDER_PATH`) to its string value."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    constants: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = node.value.value

    def resolve(arg: ast.expr) -> str | None:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        return constants.get(arg.id) if isinstance(arg, ast.Name) else None

    pairs: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_request_call = isinstance(func, ast.Attribute) and func.attr == "_request"
        if not is_request_call or len(node.args) < 2:
            continue
        method, path = resolve(node.args[0]), resolve(node.args[1])
        if method is not None and path is not None:
            pairs.add((method, path))
    return pairs


def extract_implemented_pairs(adapter_dir: Path) -> set[tuple[str, str]]:
    """(method, path) pairs under `adapter_dir`. Missing dir is valid (no OKX
    market-data/account adapter yet), same tolerance as `upbit_openapi_coverage.py`."""
    if not adapter_dir.exists():
        return set()
    pairs: set[tuple[str, str]] = set()
    for file_path in sorted(adapter_dir.rglob("*.py")):
        pairs |= _pairs_from_file(file_path.read_text(encoding="utf-8", errors="replace"))
    return pairs


def classify(method: str, path: str, implemented_pairs: set[tuple[str, str]]) -> Reason:
    if (method, path) in implemented_pairs:
        return "implemented"
    if f"{method} {path}" in _SCOPE_OVERRIDES:
        return "out_of_scope"
    return "not_started"


def build_matrix(
    reference: dict[str, Any], implemented_pairs: set[tuple[str, str]]
) -> MatrixResult:
    rows: list[EndpointRow] = []
    implemented_count = 0
    endpoints = sorted(reference["endpoints"], key=lambda e: (e["path"], e["method"]))
    for entry in endpoints:
        path, method, category, summary = _validate_entry(entry)
        reason = classify(method, path, implemented_pairs)
        if reason not in _VALID_REASONS:  # classify() already guarantees this
            raise OkxCoverageError(f"{method} {path}: disallowed reason {reason!r}")
        if reason == "implemented":
            implemented_count += 1
        rows.append(
            EndpointRow(path=path, method=method, category=category, summary=summary, reason=reason)
        )
    return MatrixResult(rows=tuple(rows), implemented=implemented_count, total=len(rows))


def render_markdown(reference: dict[str, Any], matrix: MatrixResult) -> str:
    counts: dict[str, int] = {reason: 0 for reason in _VALID_REASONS}
    for row in matrix.rows:
        counts[row.reason] += 1
    by_category: dict[str, list[EndpointRow]] = {}
    for row in matrix.rows:
        by_category.setdefault(row.category, []).append(row)

    lines = [
        "# OKX Endpoint Coverage Matrix",
        "",
        "BR-21(task-7594), ADR-2026-09-26-A. Generated by "
        "`python scripts/okx_endpoint_coverage.py` (offline, deterministic, zero "
        "network calls).",
        "",
        f"- Reference source: `{reference['source_url']}` ({reference['extraction_method']})",
        f"- Reference snapshot fetched_at: {reference['fetched_at']}",
        f"- API version: {reference['spec_version']} ({reference['spec_title']})",
        f"- Reference endpoint count: {matrix.total}",
        "",
        "Evidence for `implemented` rows: `src/exchanges/okx/trading_mixin.py` (task "
        "BR-21d, already on `main`) implements `place_order`/`cancel_order`/`modify_order` "
        "via `POST /api/v5/trade/{order,cancel-order,amend-order}`. This leaf (task-7594, "
        "BR-21a) adds only the matrix generator, not any endpoint.",
        "",
        "## Summary",
        "",
        "| implemented | out_of_scope | not_started | total | coverage |",
        "|---|---|---|---|---|",
        (
            f"| {counts['implemented']} | {counts['out_of_scope']} | "
            f"{counts['not_started']} | {matrix.total} | {matrix.percent:.2f}% |"
        ),
        "",
        "## By category",
        "",
        "| category | implemented | total | coverage |",
        "|---|---|---|---|",
    ]
    for category in sorted(by_category):
        cat_rows = by_category[category]
        impl = sum(1 for r in cat_rows if r.reason == "implemented")
        total = len(cat_rows)
        pct = round(impl / total * 100, 2) if total else 0.0
        lines.append(f"| {category} | {impl} | {total} | {pct:.2f}% |")

    lines += [
        "",
        "## Full endpoint matrix",
        "",
        "| Method | Path | Category | Status | Summary |",
        "|---|---|---|---|---|",
    ]
    for row in matrix.rows:
        summary = row.summary.replace("|", "\\|")
        lines.append(f"| {row.method} | `{row.path}` | {row.category} | {row.reason} | {summary} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-file", type=Path, default=None)
    parser.add_argument("--adapter-dir", type=Path, default=DEFAULT_ADAPTER_DIR)
    parser.add_argument("--matrix-md", type=Path, default=DEFAULT_MATRIX_MD)
    args = parser.parse_args(argv)

    try:
        reference = load_reference(args.reference_file)
        implemented_pairs = extract_implemented_pairs(args.adapter_dir)
        matrix = build_matrix(reference, implemented_pairs)
    except OkxCoverageError as exc:
        print(f"FAIL: {exc}")
        return 1

    args.matrix_md.write_text(render_markdown(reference, matrix), encoding="utf-8")
    print(
        f"OK: OKX endpoint coverage {matrix.percent:.2f}% "
        f"({matrix.implemented}/{matrix.total}) -> {args.matrix_md}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
