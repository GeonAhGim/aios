"""커버리지 래칫 — PLT-37.

절대 임계치(`--cov-fail-under`)는 두지 않는다(PM_HANDOFF §4 결정). 대신
`coverage-baseline.txt`에 적힌 직전 측정치보다 **허용 오차(기본 0.5%p)를
넘겨 하락**하면 실패하고, **상승하면 baseline을 그 값으로 갱신**한다 —
한 번 오른 커버리지는 그 아래로 조용히 떨어질 수 없다.

`coverage.xml`(Cobertura 포맷, `pytest --cov=src --cov-report=xml`가 생성)을
읽기만 한다 — 테스트를 재실행하지 않으므로 DB·네트워크 접근이 없고 로컬
CI·GitHub Actions 양쪽에서 그대로 재사용된다.

baseline 쓰기(초기화·상향 래칫)는 신뢰 가능한 측정에서만 일어난다(task-9120):
`pm/local_ci.py`의 full 모드는 프론트엔드 coverage 스텝과 달리 이 backend
스텝을 직렬화하는 락이 없어, 여러 워크트리 인스턴스가 같은 공유 호스트에서
`pytest --cov=src`를 동시에 돌리면 자원 경합으로 pytest가 중도에 죽는다 —
`lines-valid`는 최소비율 가드(0.5)를 넘길 만큼만 줄어 부분 리포트 감지는
피하면서도 line-rate가 실제와 다른 값으로 왜곡된다. 이 왜곡된 값이 그대로
baseline에 쓰이면(로컬은 write 기본 허용이었음) 다음 실제 GitHub Actions
완주 run과 어긋나 회귀 오탐(esc-ci-coverage)이 나고, `gh run download`로
진짜 값을 받아와 baseline을 손으로 되돌리는 "coverage baseline 재검증" 리프가
반복 발행됐다(task-7644/7670, task-8680, task-9052 — 24시간 안에 5회).
이제 baseline 쓰기는 `GITHUB_ACTIONS=true`(GitHub Actions가 자동 설정) 환경이거나
`--allow-baseline-write`를 명시한 호출에서만 일어난다 — 그 외(로컬 `local_ci`
자동 실행 등)는 비교만 하고 통과/실패 판정은 그대로 내리되 baseline 파일은
절대 건드리지 않는다. 기존 "gh run download 후 로컬 재검증" 교정 절차는
`--allow-baseline-write`를 붙이면 그대로 동작한다.

사용: `python scripts/coverage_ratchet.py` (저장소 루트에서, coverage.xml이
이미 생성돼 있어야 함). 종료코드 0=통과(하락 없음), 1=하락 또는 입력 오류.
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COVERAGE_XML = ROOT / "coverage.xml"
DEFAULT_BASELINE = ROOT / "coverage-baseline.txt"
DEFAULT_TOLERANCE_PP = 0.5
# coverage.py only reports files it actually imported during the measured run
# (no `[tool.coverage.run] source = src` forcing full discovery here), so a
# pytest stage that dies partway through (timeout/resource contention) leaves
# `lines-valid` far smaller than a full run's -- that shrunk denominator is
# what produces the 59~81%p false-red swings documented in ci_recheck.py. A
# ratio below this floor means the report itself is not trustworthy, so we
# refuse the comparison instead of ratcheting off a partial measurement.
DEFAULT_MIN_LINES_VALID_RATIO = 0.5


class CoverageRatchetError(ValueError):
    """coverage.xml 또는 baseline 파일 형식 오류."""


class CoverageReportNotGeneratedError(CoverageRatchetError):
    """coverage.xml is absent or 0 bytes — the report itself was never written,
    which is a different failure than a parsed report missing baseline."""


def read_current_coverage_percent(coverage_xml: Path) -> float:
    """Cobertura `coverage.xml`의 루트 `line-rate`(0..1)를 백분율로 반환한다."""
    if not coverage_xml.exists() or coverage_xml.stat().st_size == 0:
        # A 0-byte file parses as ET.ParseError("no element found: line 1, column
        # 0"), which reads like a corrupt-XML bug. It is actually the signature of
        # an upstream step (pytest) that never wrote the report at all — report
        # that distinction explicitly instead of surfacing the raw parser error.
        raise CoverageReportNotGeneratedError(
            f"커버리지 리포트가 생성되지 않았다(상류 pytest 확인): {coverage_xml}"
        )
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


def read_current_lines_valid(coverage_xml: Path) -> int | None:
    """Cobertura 루트의 `lines-valid`(측정된 전체 라인 수)를 반환한다. 속성이
    없거나 숫자가 아니면 None — 이 값은 부분 리포트 감지용 보조 신호일 뿐이라
    없다고 FAIL하지 않는다(그 자체는 `read_current_coverage_percent`가 검증)."""
    root = ET.parse(coverage_xml).getroot()
    raw = root.attrib.get("lines-valid")
    if raw is None:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def read_baseline_percent(baseline_path: Path) -> float | None:
    """baseline 파일이 없으면 None(최초 실행), 있으면 백분율 값을 반환한다."""
    if not baseline_path.exists():
        return None
    text = baseline_path.read_text(encoding="utf-8").strip()
    if not text:
        raise CoverageRatchetError(f"baseline 파일이 비어 있음: {baseline_path}")
    first_line = text.splitlines()[0]
    try:
        return round(float(first_line), 2)
    except ValueError as exc:
        raise CoverageRatchetError(f"baseline 값이 숫자가 아님: {first_line!r}") from exc


def read_baseline_lines_valid(baseline_path: Path) -> int | None:
    """baseline 파일 2번째 줄에 적힌 `lines-valid` 스냅샷. 없으면(구버전 baseline
    또는 최초 실행) None — 이 경우 부분 리포트 검사는 건너뛴다(비교 대상 부재)."""
    if not baseline_path.exists():
        return None
    lines = baseline_path.read_text(encoding="utf-8").strip().splitlines()
    if len(lines) < 2:
        return None
    try:
        return int(lines[1].strip())
    except ValueError:
        return None


def write_baseline_percent(
    baseline_path: Path, percent: float, lines_valid: int | None = None
) -> None:
    body = f"{percent:.2f}\n"
    if lines_valid is not None:
        body += f"{lines_valid}\n"
    baseline_path.write_text(body, encoding="utf-8")


def baseline_write_is_trusted(allow_baseline_write: bool, environ: dict[str, str]) -> bool:
    """baseline 파일에 쓸 수 있는 컨텍스트인지 판정한다(task-9120).

    GitHub Actions는 모든 워크플로 실행에 `GITHUB_ACTIONS=true`를 자동 주입하므로
    그 환경은 신뢰한다. 그 외(로컬 `local_ci`의 자동 full 모드 등)는 명시적으로
    `--allow-baseline-write`를 준 경우에만 쓴다 — 공유 호스트 자원 경합으로 죽은
    로컬 pytest가 만든 부분 리포트가 조용히 baseline을 오염시키는 경로를 막는다."""
    return allow_baseline_write or environ.get("GITHUB_ACTIONS") == "true"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-xml", type=Path, default=DEFAULT_COVERAGE_XML)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE_PP)
    parser.add_argument(
        "--min-lines-valid-ratio",
        type=float,
        default=DEFAULT_MIN_LINES_VALID_RATIO,
        help="baseline 대비 이 비율 미만으로 lines-valid가 줄면 부분 리포트로 보고 비교를 거부한다",
    )
    parser.add_argument(
        "--allow-baseline-write",
        action="store_true",
        help=(
            "로컬 실행에서도 baseline 파일 쓰기를 허용한다(기본은 GITHUB_ACTIONS=true"
            " 환경에서만 씀) -- gh run download로 받은 진짜 GH Actions coverage.xml로"
            " 수동 재검증할 때만 명시적으로 켠다"
        ),
    )
    args = parser.parse_args(argv)
    write_allowed = baseline_write_is_trusted(args.allow_baseline_write, dict(os.environ))

    try:
        current = read_current_coverage_percent(args.coverage_xml)
        current_lines_valid = read_current_lines_valid(args.coverage_xml)
        baseline = read_baseline_percent(args.baseline)
        baseline_lines_valid = read_baseline_lines_valid(args.baseline)
    except CoverageRatchetError as exc:
        print(f"FAIL: {exc}")
        return 1

    if baseline is None:
        if not write_allowed:
            print(
                f"SKIP: baseline 없음, 신뢰 불가 컨텍스트라 초기화하지 않는다"
                f"(측정치 {current:.2f}%, GITHUB_ACTIONS 또는 --allow-baseline-write 필요)"
            )
            return 0
        write_baseline_percent(args.baseline, current, current_lines_valid)
        print(f"BASELINE 초기화: {current:.2f}% -> {args.baseline}")
        return 0

    if (
        baseline_lines_valid is not None
        and current_lines_valid is not None
        and current_lines_valid < baseline_lines_valid * args.min_lines_valid_ratio
    ):
        print(
            f"FAIL: 부분 커버리지 리포트로 보임(측정 라인 수 lines-valid "
            f"{baseline_lines_valid} -> {current_lines_valid}, 최소 비율 "
            f"{args.min_lines_valid_ratio:.2f} 미달) — 비교를 거부한다(상류 pytest 조기중단 의심)"
        )
        return 1

    delta = current - baseline
    if delta < -args.tolerance:
        print(
            f"FAIL: 기준선 미달 {baseline:.2f}% -> {current:.2f}% "
            f"({delta:+.2f}%p, 허용 오차 {args.tolerance:.2f}%p 초과)"
        )
        return 1

    if current > baseline:
        if not write_allowed:
            print(
                f"OK: 커버리지 상승 감지 {baseline:.2f}% -> {current:.2f}% 하지만 신뢰 불가"
                f" 컨텍스트라 baseline을 갱신하지 않는다(GITHUB_ACTIONS 또는"
                f" --allow-baseline-write 필요)"
            )
            return 0
        write_baseline_percent(args.baseline, current, current_lines_valid)
        print(f"OK: 커버리지 상승, baseline 갱신 {baseline:.2f}% -> {current:.2f}%")
        return 0

    print(
        f"OK: 커버리지 {current:.2f}% "
        f"(baseline {baseline:.2f}%, 허용 오차 {args.tolerance:.2f}%p 이내)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
