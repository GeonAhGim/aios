"""KIS TR 커버리지 매트릭스 — BR-1(ADR-2026-09-06-I D2).

기준 목록은 `docs/design/kis_tr_reference.json`(`kis_tr_fetch.py`가 공식 저장소
`koreainvestment/open-trading-api`의 예제에서 기계 추출해 커밋한 스냅샷)만 읽는다.
이 스크립트 자체는 네트워크 접근이 없다 — 오프라인, CI 안전, 같은 입력에 같은 출력.

`src/exchanges/kis/*.py` 소스에 TR ID 리터럴이 문자 그대로 등장하면 구현됨으로 판정한다.
미구현 TR은 사유를 셋 중 하나로만 표기한다(ADR D2.3):
  - `실전계좌필요`: 기준 목록 안에 모의투자(V-접두) 대응 TR이 없음 — 기계 판정(KIS 명명
    규칙상 실전은 T, 모의투자는 V로 시작). `TTTC0952U`(장내채권 매수, 모의투자 미지원)처럼
    공식 예제 자체에 짝이 없는 TR이 여기 해당한다.
  - `범위밖`: `_SCOPE_OVERRIDES`에 ADR 근거와 함께 명시 등록된 TR(현재 없음).
  - `미착수`: 위 둘에 해당하지 않는 나머지 전부(기본값) — "사유 없음"은 구조적으로 존재할 수
    없다, `classify()`가 항상 이 넷 중 하나만 반환한다.

`kis-tr-coverage.txt`는 `coverage_ratchet.py`와 동일한 래칫: 직전 구현률보다 하락하면 FAIL,
상승하면 baseline을 그 값으로 갱신한다.

사용: `python scripts/kis_tr_coverage.py`(저장소 루트에서). 종료코드 0=통과, 1=하락/오류.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE = ROOT / "docs" / "design" / "kis_tr_reference.json"
DEFAULT_ADAPTER_DIR = ROOT / "src" / "exchanges" / "kis"
DEFAULT_MATRIX_MD = ROOT / "docs" / "design" / "KIS_TR_COVERAGE.md"
DEFAULT_COVERAGE_TXT = ROOT / "kis-tr-coverage.txt"
DEFAULT_TOLERANCE_PP = 0.0  # TR 개수는 정수 집합이라 커버리지 테스트와 달리 흔들릴 이유가 없다

Reason = Literal["구현됨", "실전계좌필요", "범위밖", "미착수"]
_VALID_REASONS = frozenset({"구현됨", "실전계좌필요", "범위밖", "미착수"})

# 범위밖 확정 TR — ADR이 제외를 결정하면 "tr_id: 근거"로 추가한다. 기계 추출 기준 목록
# 자체는 건드리지 않고, "목록엔 있지만 구현 대상에서 뺀다"는 결정만 여기 남긴다.
_SCOPE_OVERRIDES: dict[str, str] = {}


class KisTrCoverageError(ValueError):
    """reference/coverage 파일 형식 오류 또는 불변조건 위반."""


@dataclass(frozen=True)
class TrRow:
    tr_id: str
    domain: str
    label: str
    source_path: str
    reason: Reason


@dataclass(frozen=True)
class MatrixResult:
    rows: tuple[TrRow, ...]
    implemented: int
    total: int

    @property
    def percent(self) -> float:
        return round(self.implemented / self.total * 100, 2) if self.total else 0.0


def load_reference(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise KisTrCoverageError(
            f"기준 목록 없음: {path} — 먼저 `python scripts/kis_tr_fetch.py`로 생성"
        )
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    trs = data.get("trs")
    if not isinstance(trs, list) or not trs:
        raise KisTrCoverageError(f"기준 목록이 비어있거나 형식이 잘못됨: {path}")
    return data


def scan_adapter_source(adapter_dir: Path) -> str:
    if not adapter_dir.exists():
        raise KisTrCoverageError(f"어댑터 디렉터리 없음: {adapter_dir}")
    files = sorted(adapter_dir.rglob("*.py"))
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)


def classify(tr_id: str, all_ids: frozenset[str], implemented: bool) -> Reason:
    if implemented:
        return "구현됨"
    if tr_id in _SCOPE_OVERRIDES:
        return "범위밖"
    if tr_id[:1] == "T" and ("V" + tr_id[1:]) not in all_ids:
        return "실전계좌필요"
    return "미착수"


def build_matrix(reference: dict[str, Any], adapter_source: str) -> MatrixResult:
    trs = reference["trs"]
    all_ids = frozenset(r["tr_id"] for r in trs)
    rows: list[TrRow] = []
    implemented_count = 0
    for r in sorted(trs, key=lambda x: x["tr_id"]):
        tr_id = r["tr_id"]
        is_impl = tr_id in adapter_source
        reason = classify(tr_id, all_ids, is_impl)
        if reason not in _VALID_REASONS:  # classify()가 이미 보장하나, 회귀를 fail-closed로 잡는다
            raise KisTrCoverageError(f"{tr_id}: 허용되지 않은 사유 {reason!r}")
        implemented_count += is_impl
        rows.append(
            TrRow(
                tr_id=tr_id,
                domain=r["domain"],
                label=r["label"],
                source_path=r["source_path"],
                reason=reason,
            )
        )
    return MatrixResult(rows=tuple(rows), implemented=implemented_count, total=len(rows))


def render_markdown(reference: dict[str, Any], matrix: MatrixResult) -> str:
    counts: dict[str, int] = {reason: 0 for reason in _VALID_REASONS}
    for row in matrix.rows:
        counts[row.reason] += 1
    by_domain: dict[str, list[TrRow]] = {}
    for row in matrix.rows:
        by_domain.setdefault(row.domain, []).append(row)

    lines = [
        "# KIS TR 커버리지 매트릭스",
        "",
        "BR-1(ADR-2026-09-06-I D2). 생성: `python scripts/kis_tr_coverage.py`(오프라인, 결정적).",
        "기준 목록 갱신(네트워크 필요): `python scripts/kis_tr_fetch.py`.",
        "",
        f"- 기준 저장소: `{reference['source_repo']}` @ `{reference['source_ref']}`"
        f"(commit `{reference['source_commit'][:12]}`)",
        f"- 기준 목록 추출 시각: {reference['fetched_at']}",
        f"- 기준 TR 수: {matrix.total}개(`examples_llm/**`, `chk_*.py` 제외, 기계 추출)",
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
            "완료 정의(ADR D2): `미착수` 0건. `실전계좌필요`·`범위밖`은 남을 수 있으나 "
            "각각 사유가 있다."
        ),
        "",
        "## 도메인별",
        "",
        "| 도메인 | 구현됨 | 전체 | 구현률 |",
        "|---|---|---|---|",
    ]
    for domain in sorted(by_domain):
        domain_rows = by_domain[domain]
        impl = sum(1 for r in domain_rows if r.reason == "구현됨")
        total = len(domain_rows)
        pct = round(impl / total * 100, 2) if total else 0.0
        lines.append(f"| {domain} | {impl} | {total} | {pct:.2f}% |")

    lines += [
        "",
        "## 전체 TR 매트릭스",
        "",
        "| TR ID | 도메인 | 상태 | 이름 | 출처 |",
        "|---|---|---|---|---|",
    ]
    for row in matrix.rows:
        label = row.label.replace("|", "\\|") or "(제목 없음)"
        lines.append(
            f"| {row.tr_id} | {row.domain} | {row.reason} | {label} | `{row.source_path}` |"
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
        raise KisTrCoverageError(f"baseline 파일이 비어 있음: {path}")
    try:
        return round(float(text), 2)
    except ValueError as exc:
        raise KisTrCoverageError(f"baseline 값이 숫자가 아님: {text!r}") from exc


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지
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
    except KisTrCoverageError as exc:
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
            f"FAIL: KIS TR 커버리지 하락 {baseline:.2f}% -> {matrix.percent:.2f}% "
            f"({delta:+.2f}%p, 허용 오차 {args.tolerance:.2f}%p 초과)"
        )
        return 1

    if matrix.percent > baseline:
        args.coverage_txt.write_text(render_coverage_txt(matrix), encoding="utf-8")
        print(f"OK: 커버리지 상승, baseline 갱신 {baseline:.2f}% -> {matrix.percent:.2f}%")
        return 0

    print(
        f"OK: KIS TR 커버리지 {matrix.percent:.2f}%({matrix.implemented}/{matrix.total}, "
        f"baseline {baseline:.2f}% 이내)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
