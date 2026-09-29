"""Kiwoom Securities endpoint coverage matrix -- BR-23a (ADR-2026-09-26-A).

Same offline pattern as `nh_openapi_coverage.py` (BR-17): a checked-in
snapshot of the vendor's published endpoint list is diffed against the
adapter source, with no network calls in this script. NH's snapshot lives
in a separate file (`docs/design/nh_openapi_reference.json`) downloaded by
a companion `nh_openapi_fetch.py`. This leaf (BR-23a) is scoped to the
matrix/doc/contract-test step only -- no fetch script is in scope here --
so the snapshot instead travels as a JSON literal inside this file
(`_SNAPSHOT_JSON` below), parsed the same way a downloaded file would be.
A future leaf can add a real `kiwoom_openapi_fetch.py` and swap the
in-file literal for a downloaded reference file without changing the
matrix-building logic below.

Snapshot provenance: each endpoint was cross-checked against two
independent sources. The account/balance (`kt00001`/`kt00018`/`kt00009`)
and order (`kt10000`/`kt10001`/`kt10002`/`kt10003`) endpoints are the ones
already verified and cited in `src/exchanges/kiwoom/account_mixin.py` and
`trading_mixin.py` (verified 2026-09-26 against the official Kiwoom REST
API client repository). The quote endpoints (`ka10001`/`ka10004`/
`ka10080`) are not yet implemented by any adapter file on `main`; they are
recorded here from the public Kiwoom REST API guide
(`https://openapi.kiwoom.com/`) and independent community client
documentation for the same `api-id`s, so they show up as `not_started`
below rather than being silently missing from the matrix.

An endpoint is judged implemented if its `api_id` literal is found in
`src/exchanges/kiwoom/*.py` source. `api_id` is used rather than `path`
because several endpoints in this vendor's API share one path (three
`api_id`s under `/api/dostk/acnt`, four under `/api/dostk/ordr`) --
matching on path alone would mark all four order endpoints "implemented"
the moment any one of them was.

Usage: `python scripts/kiwoom_endpoint_coverage.py` (from the repo root).
Exit code 0 = pass (matrix written), 1 = malformed input.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ADAPTER_DIR = ROOT / "src" / "exchanges" / "kiwoom"
DEFAULT_MATRIX_MD = ROOT / "docs" / "design" / "KIWOOM_COVERAGE.md"

Reason = Literal["implemented", "not_started", "out_of_scope"]
_VALID_REASONS = frozenset({"implemented", "not_started", "out_of_scope"})

# Minimum categories required by BR-23a: quotes, account/balance, and order
# (split into new/cancel/amend so each is independently visible in the
# summary table).
_VALID_CATEGORIES = frozenset(
    {"quotes", "account_balance", "order_new", "order_cancel", "order_amend"}
)

# Endpoints confirmed out of scope by an explicit ADR decision -- empty for
# now (BR-23a's minimum three categories have no such exclusion yet), kept
# for parity with bitget_coverage.py's `_SCOPE_OVERRIDES` so a future leaf
# can register one without changing `classify()`.
_SCOPE_OVERRIDES: dict[str, str] = {}

_SNAPSHOT_JSON = """
{
  "source_url": "https://openapi.kiwoom.com/",
  "spec_version": "2025-public-rest",
  "spec_title": "Kiwoom Securities REST/WebSocket OpenAPI",
  "endpoints": [
    {"path": "/api/dostk/stkinfo", "method": "POST", "api_id": "ka10001",
     "category": "quotes", "summary": "Stock basic info request"},
    {"path": "/api/dostk/mrkcond", "method": "POST", "api_id": "ka10004",
     "category": "quotes", "summary": "Stock orderbook (quote) request"},
    {"path": "/api/dostk/chart", "method": "POST", "api_id": "ka10080",
     "category": "quotes", "summary": "Stock minute chart request"},
    {"path": "/api/dostk/acnt", "method": "POST", "api_id": "kt00001",
     "category": "account_balance", "summary": "Deposit detail request"},
    {"path": "/api/dostk/acnt", "method": "POST", "api_id": "kt00018",
     "category": "account_balance", "summary": "Account evaluation balance request"},
    {"path": "/api/dostk/acnt", "method": "POST", "api_id": "kt00009",
     "category": "account_balance", "summary": "Account order/fill status request"},
    {"path": "/api/dostk/ordr", "method": "POST", "api_id": "kt10000",
     "category": "order_new", "summary": "Buy order (new)"},
    {"path": "/api/dostk/ordr", "method": "POST", "api_id": "kt10001",
     "category": "order_new", "summary": "Sell order (new)"},
    {"path": "/api/dostk/ordr", "method": "POST", "api_id": "kt10002",
     "category": "order_amend", "summary": "Modify order"},
    {"path": "/api/dostk/ordr", "method": "POST", "api_id": "kt10003",
     "category": "order_cancel", "summary": "Cancel order"}
  ]
}
"""


class KiwoomCoverageError(ValueError):
    """Malformed snapshot/reference data or a broken invariant."""


@dataclass(frozen=True)
class EndpointRow:
    path: str
    method: str
    api_id: str
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


def load_reference(raw: str | None = None) -> dict[str, Any]:
    # `raw` defaults to the module-level snapshot at *call* time (not
    # bound at def time) so a test can monkeypatch `_SNAPSHOT_JSON` and
    # have `load_reference()` see the replacement.
    if raw is None:
        raw = _SNAPSHOT_JSON
    data: dict[str, Any] = json.loads(raw)
    endpoints = data.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        raise KiwoomCoverageError("snapshot has no endpoints -- empty or malformed reference")
    for ep in endpoints:
        category = ep.get("category")
        if category not in _VALID_CATEGORIES:
            raise KiwoomCoverageError(
                f"{ep.get('api_id')!r}: unknown category {category!r} -- expected one of "
                f"{sorted(_VALID_CATEGORIES)}"
            )
    return data


def scan_adapter_source(adapter_dir: Path = DEFAULT_ADAPTER_DIR) -> str:
    if not adapter_dir.exists():
        raise KiwoomCoverageError(f"adapter directory not found: {adapter_dir}")
    files = sorted(adapter_dir.rglob("*.py"))
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)


def classify(api_id: str, *, implemented: bool) -> Reason:
    if implemented:
        return "implemented"
    if api_id in _SCOPE_OVERRIDES:
        return "out_of_scope"
    return "not_started"


def build_matrix(reference: dict[str, Any], adapter_source: str) -> MatrixResult:
    endpoints = reference["endpoints"]
    rows: list[EndpointRow] = []
    implemented_count = 0

    for ep in sorted(endpoints, key=lambda x: (x["category"], x["api_id"])):
        api_id = ep["api_id"]
        is_impl = api_id in adapter_source
        reason = classify(api_id, implemented=is_impl)
        if reason not in _VALID_REASONS:
            raise KiwoomCoverageError(f"{api_id}: disallowed reason {reason!r}")
        if reason == "implemented":
            implemented_count += 1
        rows.append(
            EndpointRow(
                path=ep["path"],
                method=ep["method"],
                api_id=api_id,
                category=ep["category"],
                summary=ep.get("summary", ""),
                reason=reason,
            )
        )

    return MatrixResult(rows=tuple(rows), implemented=implemented_count, total=len(rows))


def render_markdown(reference: dict[str, Any], matrix: MatrixResult) -> str:
    category_counts: dict[str, int] = {cat: 0 for cat in _VALID_CATEGORIES}
    for row in matrix.rows:
        category_counts[row.category] += 1

    lines = [
        "# Kiwoom Securities Endpoint Coverage Matrix",
        "",
        "BR-23a (ADR-2026-09-26-A). Generated by "
        "`python scripts/kiwoom_endpoint_coverage.py` (offline, no network calls).",
        "",
        f"- Reference source: {reference['source_url']}",
        f"- Reference spec: {reference['spec_version']} ({reference['spec_title']})",
        f"- Reference endpoint count: {matrix.total}",
        "",
        "## Endpoint count by category",
        "",
        "| Category | Endpoints |",
        "|---|---|",
    ]
    for cat in sorted(_VALID_CATEGORIES):
        lines.append(f"| `{cat}` | {category_counts[cat]} |")
    lines.append(f"| **total** | **{matrix.total}** |")
    lines.append("")

    reason_counts: dict[str, int] = {reason: 0 for reason in _VALID_REASONS}
    for row in matrix.rows:
        reason_counts[row.reason] += 1

    lines += [
        "## Summary",
        "",
        "| implemented | not_started | out_of_scope | total | implementation rate |",
        "|---|---|---|---|---|",
        (
            f"| {reason_counts['implemented']} | {reason_counts['not_started']} | "
            f"{reason_counts['out_of_scope']} | {matrix.total} | {matrix.percent:.2f}% |"
        ),
        "",
        "## Full endpoint matrix",
        "",
        "| Category | api_id | Method | Path | Status | Summary |",
        "|---|---|---|---|---|---|",
    ]
    for row in matrix.rows:
        summary = row.summary.replace("|", "\\|") if row.summary else "(no summary)"
        lines.append(
            f"| `{row.category}` | `{row.api_id}` | {row.method} | `{row.path}` | "
            f"{row.reason} | {summary} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter-dir", type=Path, default=DEFAULT_ADAPTER_DIR)
    parser.add_argument("--matrix-md", type=Path, default=DEFAULT_MATRIX_MD)
    args = parser.parse_args(argv)

    try:
        reference = load_reference()
        adapter_source = scan_adapter_source(args.adapter_dir)
        matrix = build_matrix(reference, adapter_source)
    except KiwoomCoverageError as exc:
        print(f"FAIL: {exc}")
        return 1

    args.matrix_md.write_text(render_markdown(reference, matrix), encoding="utf-8")
    print(
        f"OK: Kiwoom endpoint coverage {matrix.percent:.2f}% "
        f"({matrix.implemented}/{matrix.total}) -> {args.matrix_md}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
