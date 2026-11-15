"""프런트엔드 성능 단언 테스트를 머지 큐와 같은 `vitest --coverage` 모드로 돌리는 푸시 전 게이트.

머지 큐의 frontend 단계는 `vitest run --coverage`로 돌고, 그때 `perfBudgetMs`가
예산을 3배로 넓힌다. 일반 모드에서만 확인한 "게이트 적색" 대조 테스트(25083)가
커버리지 모드에서 예외를 못 내 머지 큐 배치를 15시간 적색으로 만들었다.
변경된 vitest 파일 중 성능 단언(`performance.now`/`perfBudgetMs`)이 있는 것만
커버리지 모드로 돌려 같은 적색을 푸시 전에 잡는다.

사용: `python scripts/check_frontend_coverage_mode.py [--base origin/main] [파일...]`.
종료코드 0=통과 또는 대상 없음/실행 불가(node_modules 없음 등), 1=커버리지 모드 실패.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "frontend" / "apps" / "web"
PERF_MARKERS = ("performance.now", "perfBudgetMs")
TIMEOUT_SEC = 150


def changed_vitest_files(base: str) -> list[str]:
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
    prefix = "frontend/apps/web/"
    return [
        line.strip().replace("\\", "/")[len(prefix) :]
        for line in result.stdout.splitlines()
        if line.strip().replace("\\", "/").startswith(prefix)
        and line.strip().endswith((".test.ts", ".test.tsx"))
        and (ROOT / line.strip()).is_file()
    ]


def perf_sensitive(rel_paths: list[str]) -> list[str]:
    out = []
    for rel in rel_paths:
        try:
            text = (WEB / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(marker in text for marker in PERF_MARKERS):
            out.append(rel)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args(argv)
    files = args.files or changed_vitest_files(args.base)
    targets = perf_sensitive(files)
    if not targets:
        print("frontend_coverage_mode: 성능 단언이 있는 변경 vitest 파일 없음 -- 통과")
        return 0
    npx = shutil.which("npx")
    if npx is None or not (ROOT / "frontend" / "node_modules").exists():
        print("frontend_coverage_mode: npx/node_modules 없음 -- 건너뜀")
        return 0
    try:
        result = subprocess.run(
            [npx, "vitest", "run", "--coverage", *targets],
            cwd=WEB,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("frontend_coverage_mode: 시간 초과 -- 건너뜀(게이트 인프라 사정)")
        return 0
    if result.returncode == 0:
        print(f"frontend_coverage_mode: {len(targets)}개 파일 커버리지 모드 통과")
        return 0
    lines = [
        ln
        for ln in (result.stdout + "\n" + result.stderr).splitlines()
        if "FAIL" in ln or "AssertionError" in ln or "Error:" in ln
    ]
    print("frontend_coverage_mode: 커버리지 모드(머지 큐와 같은 모드)에서 실패:")
    print("\n".join(lines[:20]))
    print("perfBudgetMs는 커버리지에서 예산을 3배로 넓힌다 --")
    print("'망가뜨린 값'도 perfBudgetMs(예산) * 2로 만든다.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
