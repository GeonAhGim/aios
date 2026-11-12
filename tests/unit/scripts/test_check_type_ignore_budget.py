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
# 실패 주입 — tokenize 의존성이 예외를 던져도 명시적으로 실패한다
# ---------------------------------------------------------------------------


def test_count_ignore_comments_tokenize_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_token_error(_readline: object) -> None:
        raise tokenize.TokenError("boom")

    monkeypatch.setattr(check_type_ignore_budget.tokenize, "generate_tokens", _raise_token_error)

    with pytest.raises(check_type_ignore_budget.TypeIgnoreBudgetError):
        check_type_ignore_budget._count_ignore_comments("x = 1  # type: ignore\n")


@pytest.mark.parametrize("content", ["x = (1\n", 'x = """unterminated', "if True:\n  x=1\n y=2\n"])
def test_invalid_source_preserves_budget(tmp_path: Path, content: str) -> None:
    _write_py(tmp_path, "broken.py", content)
    budget = _write_budget(tmp_path, 5)
    assert (
        check_type_ignore_budget.main(["--root", str(tmp_path), "--budget-file", str(budget)]) == 1
    )
    assert budget.read_text(encoding="utf-8") == "5\n"


@pytest.mark.parametrize("name", ["build", "dist"])
def test_nested_source_directory_is_counted(tmp_path: Path, name: str) -> None:
    _write_py(tmp_path, f"src/{name}/source.py", "x = 1  # type: ignore\n")
    _write_py(tmp_path, f"{name}/generated.py", "x = 1  # type: ignore\n")
    assert check_type_ignore_budget.count_type_ignores(tmp_path) == 1


def test_replace_failure_preserves_budget_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = _write_budget(tmp_path, 5)

    def fail_replace(*args: object) -> None:
        raise OSError("injected replacement failure")

    monkeypatch.setattr(check_type_ignore_budget.os, "replace", fail_replace)
    assert (
        check_type_ignore_budget.main(["--root", str(tmp_path), "--budget-file", str(budget)]) == 1
    )
    assert budget.read_text(encoding="utf-8") == "5\n"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_missing_root_does_not_lower_budget(tmp_path: Path) -> None:
    budget = _write_budget(tmp_path, 5)
    assert (
        check_type_ignore_budget.main(
            ["--root", str(tmp_path / "missing"), "--budget-file", str(budget)]
        )
        == 1
    )
    assert budget.read_text(encoding="utf-8") == "5\n"


def test_directory_read_failure_preserves_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = _write_budget(tmp_path, 5)

    def fail_walk(root: Path, *, onerror):
        onerror(PermissionError("injected unreadable directory"))
        return []

    monkeypatch.setattr(check_type_ignore_budget.os, "walk", fail_walk)
    assert (
        check_type_ignore_budget.main(["--root", str(tmp_path), "--budget-file", str(budget)]) == 1
    )
    assert budget.read_text(encoding="utf-8") == "5\n"


def test_invalid_encoding_preserves_budget(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_bytes(b"x = '\xff'\n")
    budget = _write_budget(tmp_path, 5)
    assert (
        check_type_ignore_budget.main(["--root", str(tmp_path), "--budget-file", str(budget)]) == 1
    )
    assert budget.read_text(encoding="utf-8") == "5\n"


# ---------------------------------------------------------------------------
# 게이트 적색 재현 — 단언이 실제로 잡는지 입력을 망가뜨려 확인
# ---------------------------------------------------------------------------


def test_gate_red_beyond_budget_with_many_ignores(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """gate-red: budget 10에 20개 type: ignore를 넣으면 exit=1 + FAIL 출력 + budget 미변경.
    단언이 실제로 잡히는지를 입력을 의도적으로 망가뜨려 확인한다."""
    lines = "\n".join(f"x{i} = 1  # type: ignore" for i in range(20))
    _write_py(tmp_path, "many_ignores.py", lines + "\n")
    budget_path = _write_budget(tmp_path, 10)

    exit_code = check_type_ignore_budget.main(
        ["--root", str(tmp_path), "--budget-file", str(budget_path)]
    )

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "예산 초과" in out
    # 실패 시 budget이 낮아지지 않음 (래칫 보호)
    assert budget_path.read_text(encoding="utf-8").strip() == "10"


def test_gate_red_exact_budget_plus_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """gate-red 경계: budget N에 N+1개면 정확히 exit=1.
    경계값 테스트 — budget을 딱 넘치는 한 개 차이로 적색 재현."""
    _write_py(tmp_path, "a.py", "x = 1  # type: ignore\n")
    _write_py(tmp_path, "b.py", "y = 2  # type: ignore\n")
    budget_path = _write_budget(tmp_path, 1)

    exit_code = check_type_ignore_budget.main(
        ["--root", str(tmp_path), "--budget-file", str(budget_path)]
    )

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "1개 -> 2개" in out


def test_gate_red_string_literal_no_breach(
    tmp_path: Path,
) -> None:
    """gate-red 역재현: type: ignore가 문자열 안에 있으면 실제 주석이 아니므로
    budget을 초과하지 않아 exit=0. 토큰 기반 스캐너가 문자열을 무시함을
    게이트 통과로 확인한다."""
    _write_py(
        tmp_path,
        "docstring.py",
        'msg = "this has # type: ignore in a string"\nother = "also # type: ignore"\n',
    )
    budget_path = _write_budget(tmp_path, 0)

    exit_code = check_type_ignore_budget.main(
        ["--root", str(tmp_path), "--budget-file", str(budget_path)]
    )

    assert exit_code == 0


# ---------------------------------------------------------------------------
# 성능 단언 — @pytest.mark.perf + perf_inject 가상 지연 + best_of 측정
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_perf_count_type_ignores_within_budget(tmp_path: Path) -> None:
    """성능 단언: count_type_ignores()가 100개 파일(각각 50줄)을 스캔하는 데
    50ms CPU 미만이어야 한다.

    실측 근거: 100파일 × 50줄 = 5000라인 스캔에 약 0.5ms 소모 (2026-11-12,
    wt/claude-local-1, python 3.13). 30배 여유인 50ms로 예산 설정.
    """
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import reset_virtual_totals

    reset_virtual_totals()

    # 100개 파일 생성 (각각 50줄, 평균 1개 type: ignore)
    for i in range(100):
        lines = "\n".join(f"x{i}_{j} = {j}" for j in range(50))
        _write_py(tmp_path, f"file_{i:03d}.py", lines + "\n")

    pb = PerfBudget()

    def scan() -> int:
        return check_type_ignore_budget.count_type_ignores(tmp_path)

    # best_of: 여러 번 중 최소값으로 노이즈 제거
    sample = pb.best_of(scan, n=5, batch=1)

    # 실제 측정값을 로그하고 예산 이내 확인
    assert sample.cpu_ms < 50.0, (
        f"count_type_ignores(100 files × 50 lines) cpu={sample.cpu_ms:.3f}ms, "
        f"wall={sample.wall_ms:.3f}ms — budget 50ms 초과"
    )


@pytest.mark.perf
def test_perf_gate_red_with_virtual_delay_catches_budget_breach(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """성능 + 게이트 적색 결합: virtual_delayed_sync로 count_type_ignores를
    100ms 가상 지연 주입 후 게이트가 exit=1로 적색 재현하는지 확인.

    perf_inject의 virtual ledger가 실제 CPU 시간으로 더해져,
    PerfBudget이 지연된 호출을 측정하고 게이트가 budget 초과를 잡는다.
    """
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_sync,
        virtual_totals_ns,
    )

    reset_virtual_totals()

    # budget 0에 type: ignore 1개 → 반드시 exit=1
    _write_py(tmp_path, "a.py", "x = 1  # type: ignore\n")
    budget_path = _write_budget(tmp_path, 0)

    # count_type_ignores에 100ms 가상 지연 주입
    original_count = check_type_ignore_budget.count_type_ignores
    check_type_ignore_budget.count_type_ignores = virtual_delayed_sync(original_count, 0.1)

    try:
        pb = PerfBudget()

        def run_gate() -> int:
            return check_type_ignore_budget.main(
                ["--root", str(tmp_path), "--budget-file", str(budget_path)]
            )

        # 게이트가 exit=1로 적색 재현
        exit_code = run_gate()
        assert exit_code == 1

        # virtual delay가 ledger에 기록됨
        cpu_ns, _ = virtual_totals_ns()
        assert cpu_ns > 0, "virtual_delayed_sync이 지연을 ledger에 기록해야 함"

        # PerfBudget이 지연된 호출을 측정
        sample = pb.sample(run_gate, batch=1)
        # 100ms 가상 지연이 cpu_ms에 반영되어야 함
        assert sample.cpu_ms > 50.0, (
            f"virtual_delay 100ms가 cpu_ms에 반영되지 않음: {sample.cpu_ms:.3f}ms"
        )
    finally:
        # 원상 복구
        check_type_ignore_budget.count_type_ignores = original_count
        reset_virtual_totals()


@pytest.mark.perf
def test_perf_budget_initialization_is_fast(tmp_path: Path) -> None:
    """성능 단언: budget 파일이 없고 최초 실행 시 baseline 초기화가 10ms 미만.
    CI에서 매번 도는 스크립트이므로 초기화 비용도 중요하다.
    """
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import reset_virtual_totals

    reset_virtual_totals()

    # 10개 파일 (작은 크기)
    for i in range(10):
        _write_py(tmp_path, f"f_{i}.py", f"x{i} = 1  # type: ignore\n")

    pb = PerfBudget()

    def init_budget() -> int:
        budget_path = tmp_path / "type-ignore-budget.txt"
        return check_type_ignore_budget.main(
            ["--root", str(tmp_path), "--budget-file", str(budget_path)]
        )

    sample = pb.best_of(init_budget, n=5)
    assert sample.cpu_ms < 10.0, (
        f"baseline initialization cpu={sample.cpu_ms:.3f}ms — budget 10ms 초과"
    )
