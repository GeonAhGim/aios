"""`type: ignore` 예산 래칫 — PLT-40.

`coverage_ratchet.py`(PLT-37)와 동일한 래칫 메커니즘: `type-ignore-budget.txt`에
적힌 직전 측정치보다 늘어나면 FAIL, 줄어들면 baseline을 그 값으로 자동
갱신한다 — 한 번 줄어든 개수는 그 위로 조용히 늘어날 수 없다.

저장소의 `.py` 파일을 정적으로 훑어 `# type: ignore` 주석 줄 수만 센다 —
DB 접속·모듈 import 없이 텍스트 스캔만 하므로 어떤 실행 환경에서도 안전하다.

사용: `python scripts/check_type_ignore_budget.py` (저장소 루트에서).
종료코드 0=통과(예산 이내), 1=예산 초과 또는 입력 오류.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUDGET_FILE = ROOT / "type-ignore-budget.txt"

_IGNORE_COMMENT_RE = re.compile(r"#\s*type:\s*ignore\b")

# scripts/coverage_ratchet.py와 동일한 판단 — 실행 산출물·의존성 디렉터리는
# 소스가 아니므로 스캔 대상에서 제외한다.
_EXCLUDE_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        "dist",
        "build",
    }
)


class TypeIgnoreBudgetError(ValueError):
    """budget 파일 형식 오류."""


def _iter_python_files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*.py")
        if not _EXCLUDE_DIR_NAMES & set(path.relative_to(root).parts[:-1])
    ]


def _count_ignore_comments(text: str) -> int:
    """실제 주석 토큰만 센다 — 정규식으로 줄 전체를 훑으면 문자열 리터럴
    안에 이 문구를 언급하는 줄(이 스크립트 자신의 소스·테스트 픽스처 등)까지
    잘못 집계된다. `tokenize`는 문자열 리터럴과 주석을 구분하므로 실제
    억제 주석만 정확히 셀 수 있다."""
    count = 0
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT and _IGNORE_COMMENT_RE.search(tok.string):
                count += 1
    except (tokenize.TokenError, SyntaxError, IndentationError):
        # 파싱 불가한 파일은 안전하게 건너뛴다(8.3 원칙 — 모르는 상태를
        # 실패로 단정하지 않는다).
        return 0
    return count


def count_type_ignores(root: Path) -> int:
    total = 0
    for path in _iter_python_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        total += _count_ignore_comments(text)
    return total


def read_budget(budget_path: Path) -> int | None:
    """budget 파일이 없으면 None(최초 실행), 있으면 정수를 반환한다."""
    if not budget_path.exists():
        return None
    text = budget_path.read_text(encoding="utf-8").strip()
    if not text:
        raise TypeIgnoreBudgetError(f"budget 파일이 비어 있음: {budget_path}")
    try:
        return int(text)
    except ValueError as exc:
        raise TypeIgnoreBudgetError(f"budget 값이 정수가 아님: {text!r}") from exc


def write_budget(budget_path: Path, value: int) -> None:
    budget_path.write_text(f"{value}\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--budget-file", type=Path, default=DEFAULT_BUDGET_FILE)
    args = parser.parse_args(argv)

    try:
        current = count_type_ignores(args.root)
        budget = read_budget(args.budget_file)
    except TypeIgnoreBudgetError as exc:
        print(f"FAIL: {exc}")
        return 1

    if budget is None:
        write_budget(args.budget_file, current)
        print(f"BASELINE 초기화: {current}개 -> {args.budget_file}")
        return 0

    if current > budget:
        print(f"FAIL: type: ignore {budget}개 -> {current}개 (예산 초과)")
        return 1

    if current < budget:
        write_budget(args.budget_file, current)
        print(f"OK: type: ignore 개수 감소, budget 갱신 {budget}개 -> {current}개")
        return 0

    print(f"OK: type: ignore {current}개 (budget {budget}개 이내)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
