"""git diff의 FROZEN 존 침범 차단 — PLT-38.

`.aios-zone`의 FROZEN 패턴(현재 `aios/kernel/**`)에 속한 파일이
`git diff --name-only <base>..<head>` 결과에 하나라도 있으면 실패한다.
15.6-D 종료조건 전까지 FROZEN 존은 어떤 PR도 건드릴 수 없다는 정책(.aios-zone
헤더 주석)을 diff 단계에서 강제한다. FROZEN_PAPER_ONLY(`src/core/strategy|
portfolio|risk|executor`)는 이 스크립트의 차단 대상이 아니다 — PLT-38 decision.

기존 `scripts/check_zone_manifest.py`(매니페스트 자체의 정합성 검사)는
수정하지 않고, `.aios-zone`을 읽기만 공유한다.

사용: `python scripts/check_zone_diff.py --base origin/main --head HEAD`.
종료코드 0=통과(FROZEN diff 없음).
"""
from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / ".aios-zone"


def _zone_matches(pattern: str, relpath: str) -> bool:
    """check_zone_manifest.py와 동일하게 `**`를 "0개 이상의 디렉터리"로 해석한다."""
    if pattern.endswith("/**"):
        prefix = pattern[: -len("/**")]
        return relpath == prefix or relpath.startswith(prefix + "/")
    return fnmatch.fnmatch(relpath, pattern)


def load_frozen_patterns(manifest_path: Path) -> list[str]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    zones = (manifest or {}).get("zones", {})
    return list(zones.get("FROZEN", []) or [])


def git_diff_files(base: str, head: str, repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}..{head}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def find_frozen_violations(changed_files: list[str], frozen_patterns: list[str]) -> list[str]:
    return [f for f in changed_files if any(_zone_matches(p, f) for p in frozen_patterns)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="git diff의 FROZEN 존 침범 차단(PLT-38)")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path, default=None)
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지

    manifest_path = args.manifest or (args.repo / ".aios-zone")
    if not manifest_path.is_file():
        print(f"FAIL: {manifest_path} 없음")
        return 1
    frozen_patterns = load_frozen_patterns(manifest_path)

    try:
        changed_files = git_diff_files(args.base, args.head, args.repo)
    except subprocess.CalledProcessError as exc:
        print(f"FAIL: git diff 실행 실패 ({args.base}..{args.head}): {exc.stderr.strip()}")
        return 1

    violations = find_frozen_violations(changed_files, frozen_patterns)
    if violations:
        print(f"FAIL: FROZEN 존 파일이 diff에 포함됨 ({args.base}..{args.head})")
        for path in violations:
            print(f"  - {path}")
        return 1

    print(f"OK: FROZEN 존 diff 없음 ({args.base}..{args.head}, 변경 {len(changed_files)}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
