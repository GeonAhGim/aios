"""프런트엔드 타입 검사(`tsc -b`) 푸시 전 게이트 -- 2026-11-15.

머지 큐의 frontend 단계는 `npm run build`(= `tsc -b && vite build`)를 돌린다.
워커의 푸시 전 게이트는 vitest·lint만 돌려서, 미사용 import(TS6133)나 목 반환 타입
불일치(TS2322) 같은 tsc 오류가 있는 vitest 파일이 큐까지 올라가 배치를 적색으로
만들었다(25150·25183 등, 한 사이클 3.3시간). 변경된 프런트엔드 ts/tsx가 있으면 같은
`tsc -b`를 푸시 전에 돌린다(증분 약 10초).

사용: `python scripts/check_frontend_typecheck.py [--base origin/main]`.
종료코드 0=통과 또는 대상 없음/실행 불가(node_modules 없음 등), 1=타입 오류.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "frontend" / "apps" / "web"
TIMEOUT_SEC = 150


def changed_frontend_ts(base: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=AM", f"{base}...HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return []
    return [
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if line.strip().replace("\\", "/").startswith("frontend/")
        and line.strip().endswith((".ts", ".tsx"))
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="origin/main")
    args = ap.parse_args(argv)
    if not changed_frontend_ts(args.base):
        print("frontend_typecheck: 변경된 프런트엔드 ts/tsx 없음 -- 통과")
        return 0
    npx = shutil.which("npx")
    if npx is None or not (ROOT / "frontend" / "node_modules").exists():
        print("frontend_typecheck: npx/node_modules 없음 -- 건너뜀")
        return 0
    try:
        result = subprocess.run(
            [npx, "tsc", "-b"],
            cwd=WEB,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("frontend_typecheck: 시간 초과 -- 건너뜀(게이트 인프라 사정)")
        return 0
    if result.returncode == 0:
        print("frontend_typecheck: tsc -b 통과")
        return 0
    errors = [ln for ln in result.stdout.splitlines() if "error TS" in ln]
    print("frontend_typecheck: tsc -b 실패(머지 큐의 npm build와 같은 검사):")
    print("\n".join(errors[:20]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
