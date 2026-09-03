"""커버리지 래칫 — PLT-37.

절대 임계치(`--cov-fail-under`)는 두지 않는다(PM_HANDOFF §4 결정). 대신
`coverage-baseline.txt`에 적힌 직전 측정치보다 **허용 오차(기본 0.5%p)를
넘겨 하락**하면 실패하고, **상승하면 baseline을 그 값으로 갱신**한다 —
한 번 오른 커버리지는 그 아래로 조용히 떨어질 수 없다.

`coverage.xml`(Cobertura 포맷, `pytest --cov=src --cov-report=xml`가 생성)을
읽기만 한다 — 테스트를 재실행하지 않으므로 DB·네트워크 접근이 없고 로컬
CI·GitHub Actions 양쪽에서 그대로 재사용된다.

사용: `python scripts/coverage_ratchet.py` (저장소 루트에서, coverage.xml이
이미 생성돼 있어야 함). 종료코드 0=통과(하락 없음), 1=하락 또는 입력 오류.
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COVERAGE_XML = ROOT / "coverage.xml"
DEFAULT_BASELINE = ROOT / "coverage-baseline.txt"
DEFAULT_TOLERANCE_PP = 0.5


class CoverageRatchetError(ValueError):
    """coverage.xml 또는 baseline 파일 형식 오류."""


def read_current_coverage_percent(coverage_xml: Path) -> float:
    """Cobertura `coverage.xml`의 루트 `line-rate`(0..1)를 백분율로 반환한다."""
    if not coverage_xml.exists():
        raise CoverageRatchetError(f"coverage.xml 없음: {coverage_xml}")
    try:
        root = ET.parse(coverage_xml).getroot()
    except ET.ParseError as exc:
        raise CoverageRatchetError(f"coverage.xml 파싱 실패: {exc}") from exc
    line_rate = root.attrib.get("line-rate")
    if line_rate is None:
        raise CoverageRatchetError("coverage.xml: 루트에 line-rate 속성 없음")
    try:
        return round(float(line_rate) * 100, 2)
    except ValueError as exc:
        raise CoverageRatchetError(
            f"coverage.xml: line-rate 값이 숫자가 아님: {line_rate!r}"
        ) from exc


def read_baseline_percent(baseline_path: Path) -> float | None:
    """baseline 파일이 없으면 None(최초 실행), 있으면 백분율 값을 반환한다."""
    if not baseline_path.exists():
        return None
    text = baseline_path.read_text(encoding="utf-8").strip()
    if not text:
        raise CoverageRatchetError(f"baseline 파일이 비어 있음: {baseline_path}")
    try:
        return round(float(text), 2)
    except ValueError as exc:
        raise CoverageRatchetError(f"baseline 값이 숫자가 아님: {text!r}") from exc


def write_baseline_percent(baseline_path: Path, percent: float) -> None:
    baseline_path.write_text(f"{percent:.2f}\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-xml", type=Path, default=DEFAULT_COVERAGE_XML)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE_PP)
    args = parser.parse_args(argv)

    try:
        current = read_current_coverage_percent(args.coverage_xml)
        baseline = read_baseline_percent(args.baseline)
    except CoverageRatchetError as exc:
        print(f"FAIL: {exc}")
        return 1

    if baseline is None:
        write_baseline_percent(args.baseline, current)
        print(f"BASELINE 초기화: {current:.2f}% -> {args.baseline}")
        return 0

    delta = current - baseline
    if delta < -args.tolerance:
        print(
            f"FAIL: 커버리지 하락 {baseline:.2f}% -> {current:.2f}% "
            f"({delta:+.2f}%p, 허용 오차 {args.tolerance:.2f}%p 초과)"
        )
        return 1

    if current > baseline:
        write_baseline_percent(args.baseline, current)
        print(f"OK: 커버리지 상승, baseline 갱신 {baseline:.2f}% -> {current:.2f}%")
        return 0

    print(
        f"OK: 커버리지 {current:.2f}% "
        f"(baseline {baseline:.2f}%, 허용 오차 {args.tolerance:.2f}%p 이내)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
