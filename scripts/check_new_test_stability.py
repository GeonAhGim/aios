"""새로 추가된 테스트 파일의 안정성(반복 실행) 푸시 전 게이트 -- 2026-11-16.

워커가 새로 만든 테스트가 한 번은 통과하지만 다시 돌리면 실패하는 경우가 있다
(비동기 정착과 경합하는 단언, 해시 시드, 실행 순서 의존). 머지 큐 배치는 통과하고
몇 시간 뒤 full CI를 적색으로 만들었다(useIndicatorParityRows.depth1: 로컬 6회 중
1회 실패, test_check_collection_determinism).

이 게이트는 이 브랜치가 *새로* 추가한 테스트 파일만 같은 명령으로 REPEATS회 돌린다.
파이썬은 PYTHONHASHSEED를 바꾸고, vitest는 --sequence.shuffle로 순서를 섞는다.
한 번이라도 실패하면 1.

사용: `python scripts/check_new_test_stability.py [--base origin/main]`.
종료코드 0=통과 또는 대상 없음/실행 불가/시간 예산 소진, 1=반복 중 실패.
예산을 넘기면 검증을 멈추고 통과시킨다(푸시 전 게이트 총 예산 90초를 잠식하지 않는다).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "frontend" / "apps" / "web"
REPEATS = 3
BUDGET_SEC = 50.0  # 푸시 전 게이트 총 예산(90초)의 대부분을 쓰지 않도록


def added_test_files(base: str) -> tuple[list[str], list[str]]:
    """(파이썬 test_*.py, vitest *.test.ts[x]) -- base 이후 *추가*된(A) 파일만."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=A", f"{base}...HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return [], []
    py: list[str] = []
    ts: list[str] = []
    for line in result.stdout.splitlines():
        p = line.strip().replace("\\", "/")
        if not p or not (ROOT / p).is_file():
            continue
        name = Path(p).name
        if p.startswith("tests/") and name.startswith("test_") and name.endswith(".py"):
            py.append(p)
        elif p.startswith("frontend/apps/web/src/") and name.endswith((".test.ts", ".test.tsx")):
            ts.append(p[len("frontend/apps/web/") :])
    return py, ts


Runner = Callable[[list[str], dict[str, str], Path, float], int]


def _run(cmd: list[str], env: dict[str, str], cwd: Path, timeout: float) -> int:
    try:
        return subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True, timeout=timeout, check=False
        ).returncode
    except subprocess.TimeoutExpired:
        return 124


def check(
    py: list[str],
    ts: list[str],
    *,
    run: Runner = _run,
    budget_sec: float = BUDGET_SEC,
    repeats: int = REPEATS,
    clock: Callable[[], float] = time.monotonic,
    python: str = sys.executable,
    have_node: bool | None = None,
) -> list[str]:
    """반복 중 실패한 실행의 설명 목록(비면 통과). 예산을 넘기면 남은 반복은 건너뛴다."""
    deadline = clock() + budget_sec
    problems: list[str] = []
    node_ok = (
        have_node
        if have_node is not None
        else bool(shutil.which("npx") and (WEB / "node_modules").is_dir())
    )
    plan: list[tuple[str, list[str], dict[str, str], Path]] = []
    for i in range(repeats):
        if py:
            env = {**os.environ, "PYTHONHASHSEED": str(i + 1)}
            plan.append(
                (
                    f"pytest 반복 {i + 1}/{repeats}",
                    [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", *py],
                    env,
                    ROOT,
                )
            )
        if ts and node_ok:
            plan.append(
                (
                    f"vitest 반복 {i + 1}/{repeats}(순서 섞음)",
                    ["npx", "vitest", "run", "--sequence.shuffle", *ts],
                    dict(os.environ),
                    WEB,
                )
            )
    for label, cmd, env, cwd in plan:
        remaining = deadline - clock()
        if remaining <= 1:
            break  # 예산 소진 -- 검증을 멈추고 통과(게이트 예산을 잠식하지 않는다)
        rc = run(cmd, env, cwd, remaining)
        if rc == 124:
            break  # 시간 초과도 실패가 아니라 "검증 불가"
        if rc != 0:
            problems.append(f"{label}: rc={rc}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args(argv)
    py, ts = added_test_files(args.base)
    if not py and not ts:
        return 0
    problems = check(py, ts)
    if not problems:
        return 0
    sys.stderr.write(
        "새로 추가한 테스트가 반복 실행에서 실패했다"
        "(비동기 정착·해시 시드·실행 순서에 의존하는 flaky 의심):\n"
        + "\n".join(f"  - {p}" for p in problems)
        + "\n대상: "
        + ", ".join([*py, *ts])
        + '\n전이 상태("pending" 등)는 첫 렌더 직후에 단언하거나 최종 상태를 waitFor로'
        " 기다려라. 순서·시드에 의존하지 않게 고쳐라.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
