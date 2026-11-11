"""ruff format 게이트 -- `ruff format --check`를 CI·push 훅에서 강제한다.

배경(2026-11-11): CI에는 ruff 린트만 있고 format은 강제되지 않아 tests/ 345개, src/ 772개,
scripts/ 29개 파일이 어긋나 있었다(일괄 정리 3cac9e272·후속). 최근 변경분의 96%는 워커 훅
(format_on_edit.py)이 이미 포맷해서 게이트화가 현실적이다. 정리 뒤 위반 0건이라 래칫이 아니라
곧바로 게이트다: 한 건이라도 있으면 FAIL.

범위: tests · src · scripts. 단 생성기 출력(GENERATED_DIRS)은 제외한다 -- 재생성 결과와
바이트 비교하는 테스트가 있어 포맷하면 깨진다.
FROZEN_PAPER_ONLY 구역(src/core/{strategy,portfolio,risk,executor})은 사용자 승인(2026-11-11)으로
포맷-only 정리를 마쳐 범위에 포함한다.

사용: `python scripts/check_format.py [경로 ...]` (기본 tests src scripts). 종료코드 0=통과,
1=어긋난 파일 있음, 2=ruff 실행 실패. 고치는 법: `python -m ruff format <표시된 파일>`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = ("tests", "src", "scripts")
# 생성기 출력은 생성기가 낸 그대로여야 한다(test_kis_generate_adapters.py가 바이트 비교).
# 생성기가 포맷까지 내게 바뀌기 전까지 제외한다.
GENERATED_DIRS = ("src/exchanges/kis/generated",)
_OFFENDER = re.compile(r"^\s*--> (.+?):\d+:\d+\s*$", re.MULTILINE)


def check(paths: list[str], *, python: str = sys.executable) -> tuple[int, str]:
    """(종료코드, 출력). 경로가 없으면 기본 범위를 보되 생성기 출력은 뺀다."""
    cmd = [python, "-m", "ruff", "format", "--check"]
    if not paths:
        paths = list(DEFAULT_PATHS)
        for d in GENERATED_DIRS:
            cmd += ["--exclude", d]
    cmd += paths
    try:
        r = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except OSError as exc:
        return 2, f"ruff 실행 실패: {exc}"
    out = (r.stdout + r.stderr).strip()
    return (r.returncode if r.returncode in (0, 1) else 2), out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    code, out = check(list(sys.argv[1:] if argv is None else argv))
    if code == 0:
        print(f"OK: ruff format --check 통과 ({out.splitlines()[-1] if out else ''})")
        return 0
    files = sorted(set(_OFFENDER.findall(out)))
    if files:
        print(
            f"FAIL: ruff format 불일치 {len(files)}개 파일 -- "
            "`python -m ruff format <파일>`로 고쳐라"
        )
        print("\n".join(files[:30]))
    else:
        print(f"FAIL(code={code}): {out[-1500:]}")
    return code


if __name__ == "__main__":
    sys.exit(main())
