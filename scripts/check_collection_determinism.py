"""테스트 수집 순서의 결정성 게이트 -- 2026-11-14.

`@pytest.mark.parametrize("x", <frozenset/set>)`처럼 프로세스마다 순회 순서가 달라지는
값을 쓰면(문자열 해시 무작위화) xdist 워커 간 수집 순서가 어긋나 전 워커가
"Different tests were collected"로 죽는다. 머지 큐 배치의 pytest_premerge_full이 실제로
이렇게 실패했고(test_algo_vwap_depth1.py), 그 파일이 main에 들어온 뒤라 다음 full도 적색이 될
뻔했다. 변경된 테스트 파일만 해시 시드 두 개로 수집해 순서가 같은지 본다(파일 몇 개면 수 초).

사용: `python scripts/check_collection_determinism.py [--base origin/main] [파일...]`.
종료코드 0=통과, 1=위반. 파일을 주지 않으면 `git diff --name-only <base>...HEAD`의
tests/**/test_*.py를 본다.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ("1", "2")
TIMEOUT_SEC = 120


def changed_test_files(base: str) -> list[str]:
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
        if line.strip().startswith("tests/")
        and line.strip().endswith(".py")
        and Path(line.strip()).name.startswith("test_")
        and (ROOT / line.strip()).is_file()
    ]


def collected_ids(files: list[str], seed: str, root: Path = ROOT) -> list[str] | None:
    """수집된 nodeid를 순서대로. 수집 자체가 실패하면 None(이 게이트의 대상이 아니다)."""
    env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "-p",
                "no:cacheprovider",
                "--noconftest",
                "-o",
                "addopts=",
                *files,
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return [line.strip() for line in result.stdout.splitlines() if "::" in line]


def unstable_files(files: list[str], root: Path = ROOT) -> list[str]:
    first, second = (collected_ids(files, seed, root) for seed in SEEDS)
    if first is None or second is None or first == second:
        return []
    by_file: dict[str, tuple[list[str], list[str]]] = {f: ([], []) for f in files}
    for ids, slot in ((first, 0), (second, 1)):
        for nodeid in ids:
            path = nodeid.split("::")[0].replace("\\", "/")
            if path in by_file:
                by_file[path][slot].append(nodeid)
    return [f for f, (a, b) in by_file.items() if a != b]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    files = args.files or changed_test_files(args.base)
    if not files:
        print("OK: 변경된 테스트 파일 없음")
        return 0
    bad = unstable_files(files)
    if not bad:
        print(f"OK: 수집 순서 결정적 ({len(files)}개 파일, 해시 시드 {'/'.join(SEEDS)})")
        return 0
    print("FAIL: 해시 시드에 따라 수집 순서가 달라지는 테스트 파일")
    print("      (set/frozenset은 그대로 parametrize하지 말고 sorted()로):")
    for f in bad:
        print(f"  {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
