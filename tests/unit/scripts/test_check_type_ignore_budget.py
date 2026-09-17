"""scripts/check_type_ignore_budget.py 단위 테스트 — PLT-40.

DoD: 첫 측정치를 budget으로 커밋하고, `# type: ignore`가 budget보다 늘어난
합성 트리를 넣으면 exit=1로 FAIL한다. 정적 텍스트 스캔만 하는 순수 파서이므로
DB·import 없이 tmp_path에 합성한 `.py` 파일만으로 검증한다.
"""

from __future__ import annotations

import importlib.util
import sys
import tokenize
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_type_ignore_budget = _load_module(
    "check_type_ignore_budget", SCRIPTS_DIR / "check_type_ignore_budget.py"
)


def _write_py(tmp_path: Path, relative: str, content: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_budget(tmp_path: Path, value: int, name: str = "type-ignore-budget.txt") -> Path:
    path = tmp_path / name
    path.write_text(f"{value}\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 순수 스캐너 함수
# ---------------------------------------------------------------------------


def test_count_type_ignores_counts_lines_with_comment(tmp_path: Path) -> None:
    _write_py(
        tmp_path,
        "a.py",
        "x = 1  # type: ignore[attr-defined]\ny = 2\nz = 3  # type: ignore\n",
    )

    assert check_type_ignore_budget.count_type_ignores(tmp_path) == 2


def test_count_type_ignores_excludes_venv_and_pycache(tmp_path: Path) -> None:
    _write_py(tmp_path, "a.py", "x = 1  # type: ignore\n")
    _write_py(tmp_path, ".venv/lib/b.py", "y = 2  # type: ignore\n")
    _write_py(tmp_path, "src/__pycache__/c.py", "z = 3  # type: ignore\n")

    assert check_type_ignore_budget.count_type_ignores(tmp_path) == 1


def test_count_type_ignores_no_matches_is_zero(tmp_path: Path) -> None:
    _write_py(tmp_path, "a.py", "x = 1\n")

    assert check_type_ignore_budget.count_type_ignores(tmp_path) == 0


def test_read_budget_missing_file_returns_none(tmp_path: Path) -> None:
    assert check_type_ignore_budget.read_budget(tmp_path / "type-ignore-budget.txt") is None


def test_read_budget_malformed_raises(tmp_path: Path) -> None:
    path = tmp_path / "type-ignore-budget.txt"
    path.write_text("not-a-number\n", encoding="utf-8")

    with pytest.raises(check_type_ignore_budget.TypeIgnoreBudgetError):
        check_type_ignore_budget.read_budget(path)


# ---------------------------------------------------------------------------
# main() — DoD 시나리오
# ---------------------------------------------------------------------------


def test_first_run_initializes_budget_from_measurement(tmp_path: Path) -> None:
    _write_py(tmp_path, "a.py", "x = 1  # type: ignore\ny = 2  # type: ignore\n")
    budget_path = tmp_path / "type-ignore-budget.txt"

    exit_code = check_type_ignore_budget.main(
        ["--root", str(tmp_path), "--budget-file", str(budget_path)]
    )

    assert exit_code == 0
    assert budget_path.read_text(encoding="utf-8").strip() == "2"


def test_increase_beyond_budget_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_py(tmp_path, "a.py", "x = 1  # type: ignore\ny = 2  # type: ignore\n")
    budget_path = _write_budget(tmp_path, 1)

    exit_code = check_type_ignore_budget.main(
        ["--root", str(tmp_path), "--budget-file", str(budget_path)]
    )

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert budget_path.read_text(encoding="utf-8").strip() == "1"  # 실패 시 budget 미변경


def test_decrease_below_budget_ratchets_down(tmp_path: Path) -> None:
    _write_py(tmp_path, "a.py", "x = 1  # type: ignore\n")
    budget_path = _write_budget(tmp_path, 5)

    exit_code = check_type_ignore_budget.main(
        ["--root", str(tmp_path), "--budget-file", str(budget_path)]
    )

    assert exit_code == 0
    assert budget_path.read_text(encoding="utf-8").strip() == "1"


def test_equal_to_budget_passes_and_keeps_budget(tmp_path: Path) -> None:
    _write_py(tmp_path, "a.py", "x = 1  # type: ignore\n")
    budget_path = _write_budget(tmp_path, 1)

    exit_code = check_type_ignore_budget.main(
        ["--root", str(tmp_path), "--budget-file", str(budget_path)]
    )

    assert exit_code == 0
    assert budget_path.read_text(encoding="utf-8").strip() == "1"


# ---------------------------------------------------------------------------
# negative tests — 불변식 위반 입력을 명시적으로 거부한다
# ---------------------------------------------------------------------------


def test_count_type_ignores_string_literal_mention_not_counted(tmp_path: Path) -> None:
    """문자열 리터럴 안의 '# type: ignore' 언급은 억제 주석이 아니다 —
    토큰 기반 스캐너는 실제 주석 토큰만 세야 하며, 이 스크립트 자신의 소스나
    테스트 픽스처 문자열까지 잘못 집계하면 안 된다."""
    _write_py(
        tmp_path,
        "a.py",
        'doc = "this mentions # type: ignore inside a string"\n',
    )

    assert check_type_ignore_budget.count_type_ignores(tmp_path) == 0


def test_read_budget_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "type-ignore-budget.txt"
    path.write_text("", encoding="utf-8")

    with pytest.raises(check_type_ignore_budget.TypeIgnoreBudgetError):
        check_type_ignore_budget.read_budget(path)


def test_read_budget_negative_number_text_rejected_by_non_integer_variant(
    tmp_path: Path,
) -> None:
    """정수로 파싱되지 않는 값(소수점 포함)은 명시적으로 거부된다."""
    path = tmp_path / "type-ignore-budget.txt"
    path.write_text("1.5\n", encoding="utf-8")

    with pytest.raises(check_type_ignore_budget.TypeIgnoreBudgetError):
        check_type_ignore_budget.read_budget(path)


# ---------------------------------------------------------------------------
# 실패 주입 — tokenize 의존성이 예외를 던져도 안전하게 0을 반환한다
# ---------------------------------------------------------------------------


def test_count_ignore_comments_tokenize_failure_falls_back_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_token_error(_readline: object) -> None:
        raise tokenize.TokenError("boom")

    monkeypatch.setattr(check_type_ignore_budget.tokenize, "generate_tokens", _raise_token_error)

    assert check_type_ignore_budget._count_ignore_comments("x = 1  # type: ignore\n") == 0
