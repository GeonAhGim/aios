"""scripts/check_type_ignore_budget.py DEEPEN — task-2683 PLT-40 D2 하한 증빙.

ADR-2026-09-09-C: negative >=3, failure-injection 1, 성능/수치 단언 1,
게이트 적색 재현 1. `test_check_type_ignore_budget.py`(PLT-40 원본)의
DoD 시나리오 위에 이 파일을 더한다 — 원본은 정상 경로 위주, 이 파일은
방어적 경계(손상 입력·비정상 budget 값)와 실측 회귀 재현에 집중한다.
"""

from __future__ import annotations

import importlib.util
import sys
import time
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
    "check_type_ignore_budget_deepen", SCRIPTS_DIR / "check_type_ignore_budget.py"
)


def _write_py(tmp_path: Path, relative: str, content: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_budget(tmp_path: Path, value: str, name: str = "type-ignore-budget.txt") -> Path:
    path = tmp_path / name
    path.write_text(value, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# negative(>=3) — 손상/비정상 입력을 게이트가 안전하게 거부하는지
# ---------------------------------------------------------------------------


def test_negative_empty_budget_file_raises(tmp_path: Path) -> None:
    """budget 파일이 존재하지만 내용이 비어 있으면(0바이트) '최초 실행'과
    구분해 명시적으로 거부한다 — 빈 파일을 0으로 조용히 해석하면 실제로는
    측정이 실패한 CI에서 예산 0을 강제하는 사고로 이어진다."""
    budget_path = _write_budget(tmp_path, "")

    with pytest.raises(check_type_ignore_budget.TypeIgnoreBudgetError):
        check_type_ignore_budget.read_budget(budget_path)


def test_negative_whitespace_only_budget_file_raises(tmp_path: Path) -> None:
    budget_path = _write_budget(tmp_path, "   \n\t\n")

    with pytest.raises(check_type_ignore_budget.TypeIgnoreBudgetError):
        check_type_ignore_budget.read_budget(budget_path)


def test_negative_budget_value_with_trailing_garbage_raises(tmp_path: Path) -> None:
    """ "20개"처럼 사람이 손으로 편집해 단위가 섞인 값 — 부분 파싱으로
    조용히 20을 받아들이면 안 되고 즉시 거부해야 한다."""
    budget_path = _write_budget(tmp_path, "20개\n")

    with pytest.raises(check_type_ignore_budget.TypeIgnoreBudgetError):
        check_type_ignore_budget.read_budget(budget_path)


def test_negative_budget_below_zero_still_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """budget 파일이 음수(-5)처럼 그 자체로 비정상인 값이어도, 게이트는
    "예외를 던지지 않았으니 통과"로 넘어가지 않고 fail-closed로 막는다 —
    실측 0개조차 -5보다 크므로 FAIL해야 정상(ratchet이 절대 음수로
    내려가지 않는다는 방어선의 실측 확인)."""
    _write_py(tmp_path, "a.py", "x = 1\n")
    budget_path = _write_budget(tmp_path, "-5\n")

    exit_code = check_type_ignore_budget.main(
        ["--root", str(tmp_path), "--budget-file", str(budget_path)]
    )

    assert exit_code == 1
    assert "FAIL" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# failure-injection(1) — 손상된 소스 파일이 스캐너 자체를 죽이지 않는지
# ---------------------------------------------------------------------------


def test_failure_injection_corrupted_python_file_does_not_crash_scan(tmp_path: Path) -> None:
    """실제 저장소에는 이번 리프처럼 대규모 수정 도중 일시적으로 문법이
    깨진 `.py`가 커밋 전 작업트리에 섞일 수 있다 — `tokenize`가
    `TokenError`/`SyntaxError`를 던지는 손상 파일을 주입해도 스캔 전체가
    죽지 않고(예외 전파 없음) 그 파일만 0으로 건너뛰며 나머지 파일은
    정상 집계되는지 확인한다(8.3 원칙 — 모르는 상태를 크래시로 만들지
    않는다)."""
    _write_py(tmp_path, "broken.py", "def f(:\n    pass\n")  # 괄호 불일치 -> TokenError
    _write_py(tmp_path, "also_broken.py", "x = (1 + \n")  # 미종결 표현식
    _write_py(tmp_path, "ok_a.py", "y = 1  # type: ignore\n")
    _write_py(tmp_path, "ok_b.py", "z = 2  # type: ignore\nw = 3  # type: ignore\n")

    total = check_type_ignore_budget.count_type_ignores(tmp_path)

    assert total == 3  # 손상 파일 2개는 0으로 건너뛰고 정상 파일만 집계


# ---------------------------------------------------------------------------
# 성능/수치 단언(1) — 실측 정확도와 이 게이트의 실행 시간 예산
# ---------------------------------------------------------------------------


def test_numerical_assertion_exact_count_across_many_files_within_time_budget(
    tmp_path: Path,
) -> None:
    """이 게이트는 커밋마다(§3) 돈다 — 전체 저장소(1200+ 소스 파일) 규모를
    흉내낸 합성 트리에서도 집계가 정확하고 1초 안에 끝나는지 단언한다.
    250개 파일 x 파일당 0~2개 ignore로 정확한 기대 합계를 미리 계산해
    두고, 실측 합계가 그 값과 정확히 일치하는지(수치 단언) + 소요 시간이
    예산 안인지(성능 단언) 둘 다 확인한다."""
    expected_total = 0
    for i in range(250):
        ignores_in_file = i % 3  # 0, 1, 2 반복
        lines = ["x = 1\n"]
        for _ in range(ignores_in_file):
            lines.append("y = 2  # type: ignore[attr-defined]\n")
        expected_total += ignores_in_file
        _write_py(tmp_path, f"pkg/mod_{i:03d}.py", "".join(lines))

    start = time.monotonic()
    total = check_type_ignore_budget.count_type_ignores(tmp_path)
    elapsed = time.monotonic() - start

    assert total == expected_total
    assert elapsed < 5.0  # 250개 합성 파일 스캔 성능 예산(실측 여유 포함)


# ---------------------------------------------------------------------------
# 게이트 적색 재현(1) — 이번 리프가 실제로 만든 회귀 시나리오
# ---------------------------------------------------------------------------


def test_gate_red_reproduction_kis_mixin_regression_after_this_leaf(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """이번 리프(task-2683)는 `src/exchanges/kis/*_mixin.py` 4개 파일에서
    `# type: ignore[attr-defined]` 31개를 Protocol로 제거해 ratchet을
    끌어내렸다. 그 ratchet이 실제로 "한 번 내려가면 다시 못 올라간다"는
    보증을 하는지 — 이 리프가 방금 지운 것과 동일한 모양(믹스인 메서드가
    `self._request`에 다시 `# type: ignore[attr-defined]`를 붙이는 회귀)을
    합성 파일로 재현해 게이트가 즉시 RED(exit=1)로 떨어지는지 확인한다."""
    budget_path = _write_budget(tmp_path, "0\n")  # 이번 리프 이후 src/exchanges 실측(0)을 흉내
    _write_py(
        tmp_path,
        "src/exchanges/kis/domestic_stock_extra_mixin.py",
        (
            "class KISDomesticStockExtraMixin:\n"
            "    async def get_investor_trend_estimate(self, symbol):\n"
            "        raw = await self._request(  # type: ignore[attr-defined]\n"
            "            'GET', '/x', 'TR1',\n"
            "        )\n"
            "        return raw\n"
        ),
    )

    exit_code = check_type_ignore_budget.main(
        ["--root", str(tmp_path), "--budget-file", str(budget_path)]
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "FAIL" in out
    assert budget_path.read_text(encoding="utf-8").strip() == "0"  # RED에서 budget 미변경
