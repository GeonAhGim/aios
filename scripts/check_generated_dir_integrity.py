"""git diff의 `generated/` 산출물 삭제 차단 -- task-9139 (pytest 단계 24h 7회 반복 RCA).

pytest 단계가 24h 안에 7개 개별 정정 리프로 반복 발행된 원인을 역추적하면, 그중
task-8850·task-9055 두 건은 서로 다른 "정리(janitor)" 작업이 각각 독립적으로 똑같은
실수를 저지른 것이었다: `src/exchanges/kis/generated/*_tr_labels.py`가 "아무 데서도
import/문자열 참조가 없다"는 판단만으로 삭제됐고(ADR-2026-09-07-A로 한글 TR 레이블을
docstring 밖으로 뺀 의도된 산출물인데도), 이를 잡는 유일한 가드는
`tests/unit/scripts/test_kis_generate_adapters.py::
test_committed_generated_dir_matches_fresh_regeneration` 하나뿐이었다. 그 테스트는
전체 pytest 실행 안에서만 돌고, "미사용 파일 정리" task는 애초에 그 테스트 파일을
건드릴 이유가 없어 보이므로(CLAUDE.md §2 "만진 파일과 그것을 임포트하는 테스트만
지정" 규칙을 그대로 따랐을 뿐인데도) 범위를 좁힌 로컬 실행에서 가드를 우회한 채
커밋이 CI(pytest 단계)까지 올라갔다 -- 검사 로직 자체의 결함이 아니라, "생성물
삭제"라는 회귀 클래스를 잡는 가드가 diff 단계가 아니라 pytest 단계에만 있었다는
설계 위치의 문제였다(CLAUDE.md §6 사고사례 #11, 같은 실수가 task-8735/8770/8543
세 번 반복).

이 스크립트는 판정 로직을 새로 만들지 않고 git diff 메타데이터만 읽어, `generated/`
아래 파일이 base(보통 origin/main)에는 있었는데 head(작업 트리/커밋)에서 사라지면
즉시 실패한다 -- DB도 재생성 실행도 필요 없어 check_zone_diff.py와 동급으로 빠르고,
어떤 task가 어떤 파일을 "관련"으로 여기는지와 무관하게 diff 전체를 스캔한다.

OPS-42: 신규 게이트는 warn으로 먼저 등록한다(review-ops 체크리스트 #1) -- 이 저장소
자체가 아니라 fleet(ci_recheck.build_steps/.github/workflows/quality.yml) 쪽 배선이
필요하므로, 이 커밋의 task note에 "새 게이트 도입: generated_dir_integrity (warn)"를
남겨 ops가 STEP_MODE warn 등록을 하도록 한다.

사용: `python scripts/check_generated_dir_integrity.py --base origin/main --head HEAD`.
종료코드 0=통과(삭제된 generated/ 파일 없음), 1=위반 또는 git 실행 실패.
"""

from __future__ import annotations

import argparse
import subprocess  # noqa: S404
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def is_generated_path(relpath: str) -> bool:
    parts = relpath.replace("\\", "/").split("/")
    return "generated" in parts[:-1] and relpath.endswith(".py")


def git_deleted_files(base: str, head: str, repo: Path) -> list[str]:
    result = subprocess.run(  # noqa: S603, S607
        [
            "git",
            "diff",
            "--no-renames",
            "--name-status",
            f"{base}..{head}",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=result.stderr,
        )
    deleted: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or "\t" not in line:
            continue
        status, path = line.split("\t", 1)
        if status.strip() == "D":
            deleted.append(path.strip())
    return deleted


def find_deleted_generated_files(deleted_files: list[str]) -> list[str]:
    return [f for f in deleted_files if is_generated_path(f)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="git diff의 generated/ 산출물 삭제 차단(task-9139)"
    )
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지

    try:
        deleted_files = git_deleted_files(args.base, args.head, args.repo)
    except subprocess.CalledProcessError as exc:
        stderr_msg = (exc.stderr or "").strip() or f"exit code {exc.returncode}"
        print(f"FAIL: git diff 실행 실패 ({args.base}..{args.head}): {stderr_msg}")
        return 1

    violations = find_deleted_generated_files(deleted_files)
    if violations:
        print(f"FAIL: generated/ 산출물이 diff에서 삭제됨 ({args.base}..{args.head})")
        for path in violations:
            print(f"  - {path}")
        print(
            "generated/ 파일은 손으로 지우지 말고 sibling *_generate_*.py 스크립트로 "
            "재생성하라(CLAUDE.md §6 사고사례 #11)."
        )
        return 1

    print(
        f"OK: 삭제된 generated/ 파일 없음 ({args.base}..{args.head}, "
        f"삭제 {len(deleted_files)}개 중 generated/ 0개)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
