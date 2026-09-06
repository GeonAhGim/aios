"""비트겟 엔드포인트 커버리지 매트릭스 — BR-9(ADR-2026-09-06-I D5).

KIS(`kis_tr_coverage.py`, BR-1/ADR D2)와 같은 방식: 기준 목록 대 구현
소스를 기계적으로 대조해 "다 됐다"를 수치로 답한다. 비트겟은 KIS와 달리
기준이 될 공식 예제 저장소(github)가 없다 — 대신 사람이 Bitget 공식
문서(https://www.bitget.com/api-doc/)와 신뢰할 수 있는 커뮤니티 SDK
(tiagosiebler/bitget-api)를 대조해 이미 커밋해 둔 두 스펙 문서
(`docs/design/02b_bitget_api_v2_full_spec_v1.md`,
`02c_bitget_api_v2_extended_spec_v1.md`)의 Method/Path 표가 기준 목록
역할을 한다 — 이 문서들 자체가 이미 "손으로 대충 적은 목록"이 아니라
교차검증 인용을 포함한 리뷰된 산출물이다(각 문서 §0/참고문헌 참조).
이 스크립트는 그 표를 오프라인으로 파싱한다 — 네트워크 접근 없음,
같은 입력에 같은 출력.

행 추출 규칙: 마크다운 표에서 셀 값이 정확히 `GET`/`POST`/`POST/GET`인
열을 Method로 보고, 바로 다음 열의 백틱 경로들을 Path로 본다. 한 셀에
백틱이 여러 개면(예: "`/api/v2/mix/market/ticker`, `/tickers`") 처음
것만 완전한 경로이고 나머지는 직전 경로의 마지막 세그먼트를 대체하는
접미사로 본다(문서 관례) — `/api/v2/mix/market/ticker` + `/tickers`
-> `/api/v2/mix/market/tickers`.

분류(`classify()`)는 정확히 셋 중 하나만 반환한다(KIS와 동일한
fail-closed 계약, `_VALID_REASONS`로 회귀 방지):
  - `구현됨`: 어댑터 소스(`src/exchanges/bitget/**/*.py`)에 그 경로
    리터럴이 등장(`{marginType}`류 템플릿은 소스의 실제 변수명이 달라도
    "중괄호 자리표시자"로만 대조).
  - `범위밖`: 문서가 그 행에 우선순위로 "금지"를 적어둔 경우(현재
    출금 신청 1건 — 7.9 원칙, `ExchangeAdapter`는 출금 메서드를 갖지
    않는다). 문서의 결정을 그대로 반영할 뿐 이 스크립트가 새로 배제하지
    않는다.
  - `미착수`: 위 둘에 해당하지 않는 나머지 전부(기본값). P1/P2로만
    분류돼 있어도 "구현 안 됨"은 동일하게 `미착수`로 명시한다 — 우선순위
    열 자체가 이미 사유(다음 순서로 계획됨, 02b/02c §9)이므로 침묵 누락이
    아니다.

SBE·Reality Stock/Stock+(02c §0)는 Method/Path 표가 아예 없어(개념
자체가 REST 엔드포인트가 아니거나 조사 보류) 이 매트릭스에 행으로
등장하지 않는다 — 두 문서가 그 배제 사유를 이미 산문으로 명시했다.

범위: REST 엔드포인트만 다룬다. WebSocket 채널(02b §6, ticker/candle/
books/trade/account/positions/orders/orders-algo)은 Method/Path 열이
없는 별도 표라 이 스크립트로 집계되지 않는다 — 채널별 커버리지는
`tests/unit/exchanges/test_bitget_ws_messages.py`·`test_bitget_ws_parsers.py`가
개별적으로 검증한다(침묵 누락 아님, 이 문서가 범위를 명시).

`bitget-coverage.txt`는 `kis_tr_coverage.py`와 동일한 래칫: 직전
구현률보다 하락하면 FAIL, 상승하면 baseline을 그 값으로 갱신한다.

사용: `python scripts/bitget_coverage.py`(저장소 루트에서).
종료코드 0=통과, 1=하락/오류.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC_DOCS = (
    ROOT / "docs" / "design" / "02b_bitget_api_v2_full_spec_v1.md",
    ROOT / "docs" / "design" / "02c_bitget_api_v2_extended_spec_v1.md",
)
DEFAULT_ADAPTER_DIR = ROOT / "src" / "exchanges" / "bitget"
DEFAULT_MATRIX_MD = ROOT / "docs" / "design" / "BITGET_COVERAGE.md"
DEFAULT_COVERAGE_TXT = ROOT / "bitget-coverage.txt"
DEFAULT_TOLERANCE_PP = 0.0  # 엔드포인트 개수는 정수 집합 — 흔들릴 이유 없음

Reason = Literal["구현됨", "범위밖", "미착수"]
_VALID_REASONS = frozenset({"구현됨", "범위밖", "미착수"})

_METHOD_RE = re.compile(r"^(GET|POST|POST/GET|GET/POST)$")
_PATH_TOKEN_RE = re.compile(r"`([^`]+)`")
_SECTION_HEADER_RE = re.compile(r"^#{2,3}\s+(.+)$")
_PRIORITY_RE = re.compile(r"P0|P1|P2|금지")
_TEMPLATE_SEGMENT_RE = re.compile(r"\{[^}]+\}")


class BitgetCoverageError(ValueError):
    """스펙 문서/coverage 파일 형식 오류 또는 불변조건 위반."""


@dataclass(frozen=True)
class EndpointRow:
    method: str
    path: str
    label: str
    category: str
    priority: str
    source_doc: str
    reason: Reason = "미착수"  # build_matrix()가 채운다


@dataclass(frozen=True)
class MatrixResult:
    rows: tuple[EndpointRow, ...]
    implemented: int
    total: int

    @property
    def percent(self) -> float:
        return round(self.implemented / self.total * 100, 2) if self.total else 0.0


def _is_separator_row(cells: list[str]) -> bool:
    return all(set(c) <= {"-", ":"} for c in cells if c)


def _extract_priority(cells: list[str]) -> str:
    joined = " ".join(cells)
    match = _PRIORITY_RE.search(joined)
    return match.group(0) if match else ""


def parse_spec_doc(path: Path) -> list[EndpointRow]:
    if not path.exists():
        raise BitgetCoverageError(f"스펙 문서 없음: {path}")
    rows: list[EndpointRow] = []
    current_category = path.stem
    base_path: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        header = _SECTION_HEADER_RE.match(line.strip())
        if header:
            current_category = header.group(1).strip()
            base_path = None
            continue
        if not (line.startswith("|") and line.rstrip().endswith("|")):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if _is_separator_row(cells) or not cells:
            continue
        for i, cell in enumerate(cells):
            if not _METHOD_RE.match(cell):
                continue
            if i + 1 >= len(cells):
                break
            tokens = _PATH_TOKEN_RE.findall(cells[i + 1])
            if not tokens:
                break
            label = cells[0] if cells[0] else "(제목 없음)"
            priority = _extract_priority(cells[i + 2 :])
            for token in tokens:
                if token.startswith("/api/"):
                    full_path = token
                else:
                    suffix = token if token.startswith("/") else "/" + token
                    if base_path is None:
                        continue  # 선행 완전 경로 없이 접미사만 등장 — 파싱 불가, 건너뜀
                    full_path = base_path.rsplit("/", 1)[0] + suffix
                base_path = full_path
                rows.append(
                    EndpointRow(
                        method=cell,
                        path=full_path,
                        label=label,
                        category=current_category,
                        priority=priority,
                        source_doc=path.name,
                    )
                )
            break
    return rows


def load_reference(paths: tuple[Path, ...]) -> list[EndpointRow]:
    all_rows: list[EndpointRow] = []
    seen: dict[str, EndpointRow] = {}
    for path in paths:
        for row in parse_spec_doc(path):
            if row.path in seen:
                continue  # 같은 경로가 두 문서에 중복 등장 — 최초 등록만 유지
            seen[row.path] = row
            all_rows.append(row)
    if not all_rows:
        raise BitgetCoverageError(f"스펙 문서에서 엔드포인트를 하나도 추출하지 못함: {paths}")
    return all_rows


def scan_adapter_source(adapter_dir: Path) -> str:
    if not adapter_dir.exists():
        raise BitgetCoverageError(f"어댑터 디렉터리 없음: {adapter_dir}")
    files = sorted(adapter_dir.rglob("*.py"))
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)


def _path_implemented(path: str, adapter_source: str) -> bool:
    if "{" not in path:
        return path in adapter_source
    # `{marginType}` 같은 자리표시자는 소스의 실제 변수명(`{margin_type}`)과
    # 이름이 달라도 "중괄호로 둘러싸인 식별자"로만 대조한다. re.escape가
    # 중괄호까지 이스케이프하므로 템플릿 세그먼트로 미리 잘라서 조립한다.
    segments = _TEMPLATE_SEGMENT_RE.split(path)
    placeholders = _TEMPLATE_SEGMENT_RE.findall(path)
    regex_parts = [re.escape(segments[0])]
    for seg, _ph in zip(segments[1:], placeholders, strict=True):
        regex_parts.append(r"\{[^}/]+\}")
        regex_parts.append(re.escape(seg))
    pattern = "".join(regex_parts)
    return re.search(pattern, adapter_source) is not None


def classify(row: EndpointRow, implemented: bool) -> Reason:
    if implemented:
        return "구현됨"
    if row.priority == "금지":
        return "범위밖"
    return "미착수"


def build_matrix(reference: list[EndpointRow], adapter_source: str) -> MatrixResult:
    rows: list[EndpointRow] = []
    implemented_count = 0
    for row in sorted(reference, key=lambda r: r.path):
        is_impl = _path_implemented(row.path, adapter_source)
        reason = classify(row, is_impl)
        if reason not in _VALID_REASONS:  # classify()가 이미 보장 — fail-closed 회귀 방지
            raise BitgetCoverageError(f"{row.path}: 허용되지 않은 사유 {reason!r}")
        implemented_count += is_impl
        rows.append(
            EndpointRow(
                method=row.method,
                path=row.path,
                label=row.label,
                category=row.category,
                priority=row.priority,
                source_doc=row.source_doc,
                reason=reason,
            )
        )
    return MatrixResult(rows=tuple(rows), implemented=implemented_count, total=len(rows))


def render_markdown(matrix: MatrixResult) -> str:
    counts: dict[str, int] = {reason: 0 for reason in _VALID_REASONS}
    for row in matrix.rows:
        counts[row.reason] += 1
    by_category: dict[str, list[EndpointRow]] = {}
    for row in matrix.rows:
        by_category.setdefault(row.category, []).append(row)

    lines = [
        "# 비트겟 API 엔드포인트 커버리지 매트릭스",
        "",
        "BR-9(ADR-2026-09-06-I D5). 생성: `python scripts/bitget_coverage.py`"
        "(오프라인, 결정적).",
        "기준 목록: `docs/design/02b_bitget_api_v2_full_spec_v1.md` + "
        "`02c_bitget_api_v2_extended_spec_v1.md`의 Method/Path 표.",
        "",
        "범위: REST만. WebSocket 채널(02b §6)은 Method/Path 표가 아니라 "
        "`test_bitget_ws_messages.py`/`test_bitget_ws_parsers.py`가 별도로 검증한다.",
        "",
        f"- 기준 엔드포인트 수: {matrix.total}개(위 두 문서에서 기계 추출)",
        "",
        "## 요약",
        "",
        "| 구현됨 | 범위밖 | 미착수 | 합계 | 구현률 |",
        "|---|---|---|---|---|",
        (
            f"| {counts['구현됨']} | {counts['범위밖']} | {counts['미착수']} | "
            f"{matrix.total} | {matrix.percent:.2f}% |"
        ),
        "",
        (
            "완료 정의(D5): `미착수` 0건 또는 우선순위(P0/P1/P2)로 사유가 남아있음. "
            "`범위밖`은 문서가 명시한 정책 배제(7.9 원칙, 출금)만 해당한다."
        ),
        "",
        "## 카테고리별",
        "",
        "| 카테고리 | 구현됨 | 전체 | 구현률 |",
        "|---|---|---|---|",
    ]
    for category in sorted(by_category):
        cat_rows = by_category[category]
        impl = sum(1 for r in cat_rows if r.reason == "구현됨")
        total = len(cat_rows)
        pct = round(impl / total * 100, 2) if total else 0.0
        lines.append(f"| {category} | {impl} | {total} | {pct:.2f}% |")

    lines += [
        "",
        "## 전체 엔드포인트 매트릭스",
        "",
        "| Method | Path | 카테고리 | 상태 | 우선순위 | 이름 | 출처 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in matrix.rows:
        label = row.label.replace("|", "\\|")
        lines.append(
            f"| {row.method} | `{row.path}` | {row.category} | {row.reason} | "
            f"{row.priority or '-'} | {label} | `{row.source_doc}` |"
        )
    lines.append("")
    return "\n".join(lines)


def render_coverage_txt(matrix: MatrixResult) -> str:
    return f"{matrix.percent:.2f}\n"


def read_baseline_percent(path: Path) -> float | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise BitgetCoverageError(f"baseline 파일이 비어 있음: {path}")
    try:
        return round(float(text), 2)
    except ValueError as exc:
        raise BitgetCoverageError(f"baseline 값이 숫자가 아님: {text!r}") from exc


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec-docs", type=Path, nargs="+", default=list(DEFAULT_SPEC_DOCS))
    parser.add_argument("--adapter-dir", type=Path, default=DEFAULT_ADAPTER_DIR)
    parser.add_argument("--matrix-md", type=Path, default=DEFAULT_MATRIX_MD)
    parser.add_argument("--coverage-txt", type=Path, default=DEFAULT_COVERAGE_TXT)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE_PP)
    args = parser.parse_args(argv)

    try:
        reference = load_reference(tuple(args.spec_docs))
        adapter_source = scan_adapter_source(args.adapter_dir)
        matrix = build_matrix(reference, adapter_source)
        baseline = read_baseline_percent(args.coverage_txt)
    except BitgetCoverageError as exc:
        print(f"FAIL: {exc}")
        return 1

    args.matrix_md.write_text(render_markdown(matrix), encoding="utf-8")

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
            f"FAIL: 비트겟 엔드포인트 커버리지 하락 {baseline:.2f}% -> {matrix.percent:.2f}% "
            f"({delta:+.2f}%p, 허용 오차 {args.tolerance:.2f}%p 초과)"
        )
        return 1

    if matrix.percent > baseline:
        args.coverage_txt.write_text(render_coverage_txt(matrix), encoding="utf-8")
        print(f"OK: 커버리지 상승, baseline 갱신 {baseline:.2f}% -> {matrix.percent:.2f}%")
        return 0

    print(
        f"OK: 비트겟 엔드포인트 커버리지 {matrix.percent:.2f}%"
        f"({matrix.implemented}/{matrix.total}, baseline {baseline:.2f}% 이내)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
