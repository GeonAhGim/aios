"""BOM(U+FEFF) 유입 차단 게이트 -- OPS-45(task-3387).

957ae247(외부 엔진 레인 커밋)의 메시지가 UTF-8 BOM으로 시작했고, 그 전에는
tick_parquet.py 등 4개 소스/테스트 파일이 BOM 때문에 AST 기반 게이트
(compliance_gate/position_key_central)를 깼다(5a20db30에서 forward-fix). 둘 다
Claude Code 훅(.claude/hooks/)이 적용되지 않는 codex/cursor 커밋 경로에서 새어 들어왔다.

이 스크립트는 src/tests/scripts/docs 전체와 frontend 아래 각 패키지의 src/ 를 전수
스캔해 BOM으로 시작하는 파일을 찾는다. 래칫이 아니다 -- 한 건이라도 있으면 항상
FAIL(rc=1); 위반 수가 과거보다 늘지 않았다고 봐주는 baseline이 없다.

사용: `python scripts/check_no_bom.py`. 종료코드 0=통과.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOM = b"\xef\xbb\xbf"
SCAN_ROOT_NAMES = ("src", "tests", "scripts", "docs")
SKIP_DIR_NAMES = {"__pycache__", "node_modules", ".git", "dist", "build", "coverage", ".venv"}


def has_bom(path: Path) -> bool:
    """파일 선두 3바이트가 UTF-8 BOM(EF BB BF)인지만 본다 -- 내용 중간의 BOM은 대상이 아니다."""
    try:
        with path.open("rb") as fh:
            return fh.read(3) == BOM
    except OSError:
        return False


def _scan(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    found = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if SKIP_DIR_NAMES & set(path.relative_to(base).parts[:-1]):
            continue
        if has_bom(path):
            found.append(path)
    return found


def frontend_src_dirs(repo_root: Path) -> list[Path]:
    """frontend/ 아래 apps/packages 각각의 src/ 디렉터리(예: frontend/apps/web/src,
    frontend/packages/ui-web/src) -- 워크스페이스 구조상 frontend/src 단일 디렉터리는
    존재하지 않는다."""
    frontend = repo_root / "frontend"
    if not frontend.is_dir():
        return []
    return sorted({p for p in frontend.rglob("src") if p.is_dir()})


def find_bom_files(repo_root: Path) -> list[Path]:
    """repo_root 아래 전체 스캔 대상(SCAN_ROOT_NAMES + frontend의 각 src/)에서 BOM으로
    시작하는 파일 경로를 정렬해 돌려준다."""
    roots = [repo_root / name for name in SCAN_ROOT_NAMES]
    roots += frontend_src_dirs(repo_root)
    found: list[Path] = []
    for root in roots:
        found.extend(_scan(root))
    return sorted(set(found))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BOM(U+FEFF) 유입 차단 게이트(OPS-45)")
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지

    repo_root = args.repo.resolve()
    bom_files = find_bom_files(repo_root)
    if bom_files:
        print(f"FAIL: BOM(U+FEFF)으로 시작하는 파일 {len(bom_files)}건")
        for p in bom_files:
            try:
                rel = p.relative_to(repo_root)
            except ValueError:
                rel = p
            print(f"  - {rel}")
        return 1

    print(f"OK: BOM 유입 없음 ({repo_root})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
