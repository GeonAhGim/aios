"""Upbit endpoint coverage matrix -- BR-21(task-7147, CTO 2026-09-25 approval).

Reads only `docs/design/upbit_openapi_reference.json` (the snapshot
`upbit_openapi_fetch.py` produces by probing `api.upbit.com` live) as the
reference list. This script itself makes no network calls -- offline,
CI-safe, same input yields same output. Same offline/online split as
`nh_openapi_coverage.py`/`nh_openapi_fetch.py`.

An endpoint's path/method is judged implemented if found in
`src/exchanges/upbit/*.py` source. There is no Upbit adapter yet (BR-21 is
pipeline-only, no implementation) -- `src/exchanges/upbit/` does not exist,
so every endpoint is `미착수` (not-started) and the coverage rate is 0%.
Unlike `nh_openapi_coverage.py`, `scan_adapter_source` here tolerates a
missing adapter directory instead of failing, since "no adapter exists yet"
is the expected state for this leaf.

Usage: `python scripts/upbit_openapi_coverage.py` (from the repo root). Exit
code 0 = pass, 1 = drop/error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE = ROOT / "docs" / "design" / "upbit_openapi_reference.json"
DEFAULT_ADAPTER_DIR = ROOT / "src" / "exchanges" / "upbit"
DEFAULT_MATRIX_MD = ROOT / "docs" / "design" / "UPBIT_COVERAGE.md"
DEFAULT_COVERAGE_TXT = ROOT / "upbit-coverage.txt"
DEFAULT_TOLERANCE_PP = 0.0

Reason = Literal["구현됨", "실전계좌필요", "범위밖", "미착수"]
_VALID_REASONS = frozenset({"구현됨", "실전계좌필요", "범위밖", "미착수"})

# ADR로 제외를 결정하면 "path method: 사유" 형태로 추가한다 (현재 없음).
_SCOPE_OVERRIDES: dict[str, str] = {}


class UpbitCoverageError(ValueError):
    """Malformed reference/coverage file or an invariant violation."""


@dataclass(frozen=True)
class EndpointRow:
    path: str
    method: str
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


def load_reference(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise UpbitCoverageError(
            f"기준 목록 없음: {path} — 먼저 `python scripts/upbit_openapi_fetch.py`로 생성"
        )
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    endpoints = data.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        raise UpbitCoverageError(f"기준 목록이 비어있거나 형식이 잘못됨: {path}")
    return data


def scan_adapter_source(adapter_dir: Path) -> str:
    """Missing adapter dir is a valid state here (no Upbit adapter exists yet)."""
    if not adapter_dir.exists():
        return ""
    files = sorted(adapter_dir.rglob("*.py"))
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)


def classify(path: str, method: str, implemented: bool) -> Reason:
    """Classify an endpoint.

    - implemented: path literal found in source
    - out-of-scope: registered in _SCOPE_OVERRIDES
    - not-started: everything else (default) -- includes 'no adapter at all'
    """
    if implemented:
        return "구현됨"

    key = f"{path} {method}"
    if key in _SCOPE_OVERRIDES:
        return "범위밖"

    return "미착수"


def build_matrix(reference: dict[str, Any], adapter_source: str) -> MatrixResult:
    endpoints = reference["endpoints"]
    rows: list[EndpointRow] = []
    implemented_count = 0

    for ep in sorted(endpoints, key=lambda x: (x["path"], x["method"])):
        path = ep["path"]
        method = ep["method"]
        summary = ep.get("summary", "")

        is_impl = bool(adapter_source) and path in adapter_source

        reason = classify(path, method, is_impl)
        if reason not in _VALID_REASONS:
            raise UpbitCoverageError(f"{path} {method}: 허용되지 않은 사유 {reason!r}")

        if reason == "구현됨":
            implemented_count += 1

        rows.append(EndpointRow(path=path, method=method, summary=summary, reason=reason))

    return MatrixResult(rows=tuple(rows), implemented=implemented_count, total=len(rows))


def render_markdown(reference: dict[str, Any], matrix: MatrixResult) -> str:
    counts: dict[str, int] = {reason: 0 for reason in _VALID_REASONS}
    for row in matrix.rows:
        counts[row.reason] += 1

    lines = [
        "# Upbit 엔드포인트 커버리지 매트릭스",
        "",
        (
            "BR-21(task-7147, CTO 2026-09-25 승인). 생성: "
            "`python scripts/upbit_openapi_coverage.py`(오프라인)."
        ),
        "기준 목록 갱신(네트워크 필요): `python scripts/upbit_openapi_fetch.py`.",
        "",
        "파이프라인 선제 검증 전용 매트릭스다 -- 구현 없음, 전부 `미착수`(BR-18 참고,",
        "`docs/exchanges/ADDING_AN_EXCHANGE.md`). 실제 어댑터 구현은 별도 task.",
        "",
        f"- 기준 소스: Upbit 공식 문서 `{reference['source_url']}`",
        f"- 추출 방식: {reference['extraction_method']}",
        f"- 기준 목록 추출 시각: {reference['fetched_at']}",
        f"- 기준 엔드포인트 수: {matrix.total}개",
        f"- API 버전: {reference['spec_version']} ({reference['spec_title']})",
        "",
        "## 요약",
        "",
        "| 구현됨 | 실전계좌필요 | 범위밖 | 미착수 | 합계 | 구현률 |",
        "|---|---|---|---|---|---|",
        (
            f"| {counts['구현됨']} | {counts['실전계좌필요']} | {counts['범위밖']} | "
            f"{counts['미착수']} | {matrix.total} | {matrix.percent:.2f}% |"
        ),
        "",
        (
            "완료 정의(BR-18 패턴 재사용): `미착수` 0건이 되려면 실제 `src/exchanges/upbit/` "
            "어댑터 구현(별도 task)이 선행돼야 한다."
        ),
        "",
        "## 전체 엔드포인트 매트릭스",
        "",
        "| 경로 | 메서드 | 상태 | 설명 |",
        "|---|---|---|---|",
    ]

    for row in matrix.rows:
        summary = row.summary.replace("|", "\\|") if row.summary else "(설명 없음)"
        lines.append(f"| `{row.path}` | {row.method} | {row.reason} | {summary} |")

    lines.append("")
    return "\n".join(lines)


def render_coverage_txt(matrix: MatrixResult) -> str:
    return f"{matrix.percent:.2f}\n"


def read_baseline_percent(path: Path) -> float | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise UpbitCoverageError(f"baseline 파일이 비어 있음: {path}")
    try:
        return round(float(text), 2)
    except ValueError as exc:
        raise UpbitCoverageError(f"baseline 값이 숫자가 아님: {text!r}") from exc


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--adapter-dir", type=Path, default=DEFAULT_ADAPTER_DIR)
    parser.add_argument("--matrix-md", type=Path, default=DEFAULT_MATRIX_MD)
    parser.add_argument("--coverage-txt", type=Path, default=DEFAULT_COVERAGE_TXT)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE_PP)
    args = parser.parse_args(argv)

    try:
        reference = load_reference(args.reference)
        adapter_source = scan_adapter_source(args.adapter_dir)
        matrix = build_matrix(reference, adapter_source)
        baseline = read_baseline_percent(args.coverage_txt)
    except UpbitCoverageError as exc:
        print(f"FAIL: {exc}")
        return 1

    args.matrix_md.write_text(render_markdown(reference, matrix), encoding="utf-8")

    if baseline is None:
        args.coverage_txt.write_text(render_coverage_txt(matrix), encoding="utf-8")
        print(
            f"BASELINE 초기화: {matrix.percent:.2f}%({matrix.implemented}/{matrix.total}) "
            f"-> {args.coverage_txt}"
        )
        return 0

    delta = matrix.percent - baseline
    if delta < -args.tolerance:
        print(
            f"FAIL: Upbit 엔드포인트 커버리지 하락 {baseline:.2f}% -> {matrix.percent:.2f}% "
            f"({delta:+.2f}%p, 허용 오차 {args.tolerance:.2f}%p 초과)"
        )
        return 1

    if matrix.percent > baseline:
        args.coverage_txt.write_text(render_coverage_txt(matrix), encoding="utf-8")
        print(f"OK: 커버리지 상승, baseline 갱신 {baseline:.2f}% -> {matrix.percent:.2f}%")
        return 0

    print(
        f"OK: Upbit 엔드포인트 커버리지 {matrix.percent:.2f}%({matrix.implemented}/{matrix.total}, "
        f"baseline {baseline:.2f}% 이내)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
