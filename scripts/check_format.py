"""tests/ 포맷 게이트 -- `ruff format --check`를 CI·push 훅에서 강제한다.

배경(2026-11-11): CI에는 ruff 린트만 있고 format은 강제되지 않아 tests/ 345개 파일이 어긋나 있었다
(일괄 정리 3cac9e272). 최근 4일 변경분의 96%는 워커 훅(format_on_edit.py)이 이미 포맷해서
게이트화가 현실적이다. 정리 뒤 위반 0건이라 래칫이 아니라 곧바로 게이트다: 한 건이라도 있으면 FAIL.

범위는 tests/ 뿐이다 -- src/와 scripts/는 어긋난 파일이 많고, src/의 보호 경로
(core/strategy·portfolio·risk·executor)는 FROZEN 승인 기록 없이 건드릴 수 없다.

사용: `python scripts/check_format.py [경로 ...]` (기본 tests). 종료코드 0=통과, 1=어긋난 파일 있음,
2=ruff 실행 실패. 고치는 법: `python -m ruff format <표시된 파일>`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = ("tests",)


def check(paths: list[str], *, python: str = sys.executable) -> tuple[int, str]:
    """(종료코드, 출력). 경로가 없으면 기본 범위(tests)를 본다."""
    cmd = [python, "-m", "ruff", "format", "--check", *(paths or list(DEFAULT_PATHS))]
    try:
        r = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except OSError as exc:
        return 2, f"ruff 실행 실패: {exc}"
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0:
        return 0, out
    if r.returncode == 1:
        return 1, out
    return 2, out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    code, out = check(list(sys.argv[1:] if argv is None else argv))
    if code == 0:
        print(f"OK: ruff format --check 통과 ({out.splitlines()[-1] if out else ''})")
        return 0
    lines = [ln for ln in out.splitlines() if ln.startswith("Would reformat")]
    n = len(lines) or "?"
    print(f"FAIL: ruff format 불일치 {n}개 파일 -- `python -m ruff format <파일>`로 고쳐라")
    print("\n".join(lines[:20]) if lines else out[-1500:])
    return code


if __name__ == "__main__":
    sys.exit(main())
