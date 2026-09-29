"""Binance public REST API endpoint coverage matrix -- BR-22a (task-7599).

Spec: ADR-2026-09-26-A (exchange expansion -- Binance). Follows the offline
snapshot pattern from `docs/exchanges/ADDING_AN_EXCHANGE.md` SS4/SS5 (same
shape as `nh_openapi_coverage.py`/`bitget_coverage.py`): a reference list of
endpoints is captured once, offline, and a coverage script reads only that
snapshot to produce a matrix -- zero network calls, same input yields the
same output every time.

Binance does not publish a single machine-readable OpenAPI/Swagger document
covering both Spot and USDS-M Futures (unlike NH's `openapi.json`), so this
follows the Bitget precedent instead: a hand-curated reference derived from
the officially published REST API docs (
https://binance-docs.github.io/apidocs/spot/en/,
https://binance-docs.github.io/apidocs/futures/en/), captured as of
2026-09-26. The snapshot is embedded below as a frozen JSON literal
(`SNAPSHOT_JSON`) rather than a separate committed file, so this leaf's
surface stays exactly the three files task-7599 lists -- `load_snapshot()`
still parses it through `json.loads`, so the "reads only a snapshot JSON,
no network calls" contract holds structurally, not just in spirit.

This module intentionally does not scan any exchange adapter source (unlike
`bitget_coverage.py`/`nh_openapi_coverage.py`, which cross-reference
`src/exchanges/<venue>/`): task-7599 is scoped to the endpoint matrix and its
contract-test generator only, and does not touch `src/exchanges/binance/`.
The DoD asks for endpoint counts by category, not an implemented/not-started
split against adapter code.

Usage: `python scripts/binance_endpoint_coverage.py` (from the repo root).
Exit code 0 = pass (matrix written), 1 = the snapshot violates its contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX_MD = ROOT / "docs" / "design" / "BINANCE_COVERAGE.md"

# DoD-minimum categories/subcategories (task-7599): public market data
# (ticker/orderbook/candles), account (balance/positions), order
# (create/cancel/query). A snapshot that drops any of these fails closed in
# `_assert_minimum_coverage`.
REQUIRED_CATEGORIES: dict[str, frozenset[str]] = {
    "market_data": frozenset({"ticker", "orderbook", "candles"}),
    "account": frozenset({"balance", "positions"}),
    "order": frozenset({"create", "cancel", "query"}),
}

_VALID_METHODS = frozenset({"GET", "POST", "PUT", "DELETE"})
_REQUIRED_ENTRY_FIELDS = ("method", "path", "category", "subcategory", "summary")

# Offline snapshot -- see module docstring. Frozen literal, no network call.
SNAPSHOT_JSON = """
{
  "source": "Binance Spot + USDS-M Futures public REST API docs (offline curation)",
  "captured_at": "2026-09-26T00:00:00Z",
  "endpoints": [
    {"method": "GET", "path": "/api/v3/ticker/price", "category": "market_data",
     "subcategory": "ticker", "summary": "Latest price for a symbol or symbols"},
    {"method": "GET", "path": "/api/v3/ticker/24hr", "category": "market_data",
     "subcategory": "ticker", "summary": "24hr rolling window price change statistics"},
    {"method": "GET", "path": "/api/v3/depth", "category": "market_data",
     "subcategory": "orderbook", "summary": "Order book depth"},
    {"method": "GET", "path": "/api/v3/klines", "category": "market_data",
     "subcategory": "candles", "summary": "Kline/candlestick bars"},
    {"method": "GET", "path": "/api/v3/account", "category": "account",
     "subcategory": "balance", "summary": "Spot account information (signed)"},
    {"method": "GET", "path": "/fapi/v2/balance", "category": "account",
     "subcategory": "balance", "summary": "USDS-M futures account balance (signed)"},
    {"method": "GET", "path": "/fapi/v2/positionRisk", "category": "account",
     "subcategory": "positions", "summary": "USDS-M futures position information (signed)"},
    {"method": "POST", "path": "/api/v3/order", "category": "order",
     "subcategory": "create", "summary": "Place a new spot order (signed)"},
    {"method": "DELETE", "path": "/api/v3/order", "category": "order",
     "subcategory": "cancel", "summary": "Cancel an active spot order (signed)"},
    {"method": "GET", "path": "/api/v3/order", "category": "order",
     "subcategory": "query", "summary": "Query a spot order's status (signed)"},
    {"method": "GET", "path": "/api/v3/openOrders", "category": "order",
     "subcategory": "query", "summary": "Query all open spot orders (signed)"}
  ]
}
"""


class BinanceCoverageError(ValueError):
    """Malformed snapshot or a minimum-coverage invariant violation."""


@dataclass(frozen=True)
class EndpointRow:
    method: str
    path: str
    category: str
    subcategory: str
    summary: str


@dataclass(frozen=True)
class MatrixResult:
    rows: tuple[EndpointRow, ...]
    category_counts: dict[str, int]
    subcategory_counts: dict[str, dict[str, int]]

    @property
    def total(self) -> int:
        return len(self.rows)


def load_snapshot(text: str = SNAPSHOT_JSON) -> dict[str, Any]:
    """Parse the offline snapshot text. Raises on malformed JSON or shape."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BinanceCoverageError(f"snapshot is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or "endpoints" not in data:
        raise BinanceCoverageError("snapshot missing 'endpoints' key")
    endpoints = data["endpoints"]
    if not isinstance(endpoints, list) or not endpoints:
        raise BinanceCoverageError("snapshot 'endpoints' must be a non-empty list")
    return data


def _validate_entry(entry: dict[str, Any]) -> EndpointRow:
    for field in _REQUIRED_ENTRY_FIELDS:
        if not entry.get(field):
            raise BinanceCoverageError(f"endpoint entry missing required field {field!r}: {entry}")
    method = entry["method"]
    if method not in _VALID_METHODS:
        raise BinanceCoverageError(f"invalid HTTP method {method!r} in entry: {entry}")
    path = entry["path"]
    if not path.startswith("/"):
        raise BinanceCoverageError(f"path must start with '/': {path!r}")
    category = entry["category"]
    if category not in REQUIRED_CATEGORIES:
        raise BinanceCoverageError(
            f"unknown category {category!r} (expected one of {sorted(REQUIRED_CATEGORIES)})"
        )
    return EndpointRow(
        method=method,
        path=path,
        category=category,
        subcategory=entry["subcategory"],
        summary=entry["summary"],
    )


def _assert_minimum_coverage(subcategory_counts: dict[str, dict[str, int]]) -> None:
    """Fail closed if the snapshot no longer covers the DoD's minimum categories."""
    for category, required_subs in REQUIRED_CATEGORIES.items():
        present = set(subcategory_counts.get(category, {}))
        missing = required_subs - present
        if missing:
            raise BinanceCoverageError(
                f"category {category!r} missing required subcategories {sorted(missing)}"
            )


def build_matrix(snapshot: dict[str, Any]) -> MatrixResult:
    rows: list[EndpointRow] = []
    seen: set[tuple[str, str]] = set()
    for entry in snapshot["endpoints"]:
        row = _validate_entry(entry)
        key = (row.method, row.path)
        if key in seen:
            raise BinanceCoverageError(f"duplicate endpoint {key} in snapshot")
        seen.add(key)
        rows.append(row)

    category_counts: dict[str, int] = dict.fromkeys(REQUIRED_CATEGORIES, 0)
    subcategory_counts: dict[str, dict[str, int]] = {c: {} for c in REQUIRED_CATEGORIES}
    for row in rows:
        category_counts[row.category] += 1
        bucket = subcategory_counts[row.category]
        bucket[row.subcategory] = bucket.get(row.subcategory, 0) + 1

    _assert_minimum_coverage(subcategory_counts)
    sorted_rows = tuple(sorted(rows, key=lambda r: (r.category, r.subcategory, r.path)))
    return MatrixResult(
        rows=sorted_rows,
        category_counts=category_counts,
        subcategory_counts=subcategory_counts,
    )


def render_markdown(matrix: MatrixResult, snapshot: dict[str, Any]) -> str:
    lines = [
        "# 바이낸스 API 엔드포인트 커버리지 매트릭스",
        "",
        "BR-22a(ADR-2026-09-26-A, task-7599). 생성: "
        "`python scripts/binance_endpoint_coverage.py`(오프라인, 결정적, 네트워크 호출 0건).",
        f"- 기준 소스: {snapshot['source']}",
        f"- 스냅샷 캡처 시각: {snapshot['captured_at']}",
        f"- 기준 엔드포인트 수: {matrix.total}개",
        "",
        "범위: 이 매트릭스는 원본 거래소가 공개한 REST 엔드포인트 수만 집계한다 "
        "-- `src/exchanges/binance/`의 구현 여부는 대조하지 않는다(task-7599 범위 "
        "밖, 최소 카테고리 커버리지만 확인).",
        "",
        "## 카테고리별 엔드포인트 수",
        "",
        "| 카테고리 | 엔드포인트 수 | 하위 분류 |",
        "|---|---|---|",
    ]
    for category in sorted(matrix.category_counts):
        subs = matrix.subcategory_counts[category]
        sub_desc = ", ".join(f"{name}={count}" for name, count in sorted(subs.items()))
        lines.append(f"| {category} | {matrix.category_counts[category]} | {sub_desc} |")

    lines += [
        "",
        "## 전체 엔드포인트 매트릭스",
        "",
        "| Method | Path | 카테고리 | 하위 분류 | 설명 |",
        "|---|---|---|---|---|",
    ]
    for row in matrix.rows:
        summary = row.summary.replace("|", "\\|")
        lines.append(
            f"| {row.method} | `{row.path}` | {row.category} | {row.subcategory} | {summary} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix-md", type=Path, default=DEFAULT_MATRIX_MD)
    args = parser.parse_args(argv)

    try:
        snapshot = load_snapshot()
        matrix = build_matrix(snapshot)
    except BinanceCoverageError as exc:
        print(f"FAIL: {exc}")
        return 1

    args.matrix_md.write_text(render_markdown(matrix, snapshot), encoding="utf-8")
    print(
        f"OK: 바이낸스 엔드포인트 커버리지 매트릭스 {matrix.total}개 "
        f"({', '.join(f'{c}={n}' for c, n in sorted(matrix.category_counts.items()))}) "
        f"-> {args.matrix_md}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
