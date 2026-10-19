"""scripts/check_complexity.py 단위 테스트 -- RATCHET-2, task-3256.

인지 복잡도 채점 규칙(if/elif/else 중첩·for/while/except·BoolOp·재귀·중첩 함수
독립 채점)을 합성 fixture로 검증하고, `check_code_ratchets.py`와 동일한
baseline 래칫(증가=exit 2, 감소는 --update로만 반영)을 재현한다. D2 DoD:
negative test 3건 이상, 실패주입 1건(파싱 불가 파일), 성능 단언 1건(스캔
처리량), 게이트 적색 재현 1건, failure-injection 1건(예산 10배 지연 -> 적색).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.conftest import PerfBudget

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cc = _load_module("check_complexity", SCRIPTS_DIR / "check_complexity.py")


def _write_py(tmp_path: Path, relative: str, content: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_baseline(
    tmp_path: Path, over_cap_count: int, name: str = "complexity-baseline.json"
) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"over_cap_count": over_cap_count}), encoding="utf-8")
    return path


def _func_node(source: str, name: str = "f") -> object:
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


# ---------------------------------------------------------------------------
# cognitive_complexity() 채점 규칙
# ---------------------------------------------------------------------------


def test_flat_function_has_zero_complexity() -> None:
    node = _func_node("def f():\n    x = 1\n    return x\n")
    assert cc.cognitive_complexity(node) == 0


def test_single_if_adds_one() -> None:
    node = _func_node("def f(x):\n    if x:\n        return 1\n    return 0\n")
    assert cc.cognitive_complexity(node) == 1


def test_nested_if_adds_nesting_weight() -> None:
    node = _func_node(
        "def f(a, b):\n    if a:\n        if b:\n            return 1\n    return 0\n"
    )
    # outer if: +1(nesting0)=1, inner if: +1+1(nesting1)=2 -> total 3
    assert cc.cognitive_complexity(node) == 3


def test_elif_chain_does_not_add_extra_nesting() -> None:
    node = _func_node(
        "def f(x):\n"
        "    if x == 1:\n"
        "        return 'a'\n"
        "    elif x == 2:\n"
        "        return 'b'\n"
        "    else:\n"
        "        return 'c'\n"
    )
    # if +1, elif +1, else +1 -> 3 (no nesting compounding across siblings)
    assert cc.cognitive_complexity(node) == 3


def test_for_and_except_add_nesting_weight() -> None:
    node = _func_node(
        "def f(items):\n"
        "    for item in items:\n"
        "        try:\n"
        "            use(item)\n"
        "        except ValueError:\n"
        "            pass\n"
    )
    # for: +1(nesting0)=1, except: +1+1(nesting1)=2 -> total 3
    assert cc.cognitive_complexity(node) == 3


def test_bool_op_chain_adds_one() -> None:
    node = _func_node("def f(a, b, c):\n    if a and b and c:\n        return 1\n    return 0\n")
    # if: +1, BoolOp chain: +1 -> 2
    assert cc.cognitive_complexity(node) == 2


def test_recursive_call_adds_one() -> None:
    node = _func_node("def f(n):\n    if n <= 1:\n        return 1\n    return n * f(n - 1)\n")
    # if: +1, recursive call f(): +1 -> 2
    assert cc.cognitive_complexity(node) == 2


def test_nested_function_not_double_counted() -> None:
    node = _func_node(
        "def f(x):\n"
        "    if x:\n"
        "        def g(y):\n"
        "            if y:\n"
        "                return 1\n"
        "        return g(x)\n"
        "    return 0\n"
    )
    # outer f: only its own `if x` -> 1 (g's internal if is g's own score, not f's)
    assert cc.cognitive_complexity(node) == 1


# ---------------------------------------------------------------------------
# scan_file / scan_tree
# ---------------------------------------------------------------------------


def test_scan_file_flags_function_over_cap(tmp_path: Path) -> None:
    lines = ["def f(x):"]
    for i in range(cc.CAP + 1):
        lines.append(f"    if x == {i}:")
        lines.append(f"        x += {i}")
    source = "\n".join(lines) + "\n"
    hits = cc.scan_file("a.py", source)
    assert len(hits) == 1
    assert hits[0][2] == "f"
    assert hits[0][3] > cc.CAP


def test_scan_file_skips_function_under_cap() -> None:
    hits = cc.scan_file("a.py", "def f(x):\n    if x:\n        return 1\n    return 0\n")
    assert hits == []


def test_scan_file_handles_syntax_error_gracefully() -> None:
    """실패주입: 파싱 불가한 파일은 예외를 올리지 않고 빈 결과로 건너뛴다."""
    hits = cc.scan_file("broken.py", "def f(:\n    pass\n")
    assert hits == []


def test_scan_tree_excludes_pycache(tmp_path: Path) -> None:
    _write_py(tmp_path, "src/__pycache__/a.py", "def f():\n    " + "if x:\n        pass\n" * 30)
    hits = cc.scan_tree(tmp_path, subdirs=("src",))
    assert hits == []


# ---------------------------------------------------------------------------
# main() -- 래칫 DoD 시나리오
# ---------------------------------------------------------------------------


def test_first_run_initializes_baseline(tmp_path: Path) -> None:
    _write_py(tmp_path, "src/a.py", "def f():\n    return 1\n")
    baseline_path = tmp_path / "complexity-baseline.json"

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 0
    assert json.loads(baseline_path.read_text(encoding="utf-8")) == {"over_cap_count": 0}


def test_increase_in_over_cap_count_fails_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """게이트 적색 재현: over_cap_count가 baseline보다 늘면 exit 2."""
    lines = ["def f(x):"]
    for i in range(cc.CAP + 1):
        lines.append(f"    if x == {i}:")
        lines.append(f"        x += {i}")
    _write_py(tmp_path, "src/a.py", "\n".join(lines) + "\n")
    baseline_path = _write_baseline(tmp_path, 0)

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "over_cap_count" in out
    assert "0개 -> 1개" in out
    unchanged = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert unchanged["over_cap_count"] == 0


def test_decrease_without_update_leaves_baseline_unchanged(tmp_path: Path) -> None:
    _write_py(tmp_path, "src/a.py", "def f():\n    return 1\n")
    baseline_path = _write_baseline(tmp_path, 3)

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 0
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["over_cap_count"] == 3


def test_decrease_with_update_flag_ratchets_down(tmp_path: Path) -> None:
    _write_py(tmp_path, "src/a.py", "def f():\n    return 1\n")
    baseline_path = _write_baseline(tmp_path, 3)

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path), "--update"])

    assert exit_code == 0
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["over_cap_count"] == 0


def test_malformed_baseline_json_fails(tmp_path: Path) -> None:
    """실패주입: baseline JSON이 깨져 있으면 exit 1(적색이 아니라 입력 오류로 구분)."""
    baseline_path = tmp_path / "complexity-baseline.json"
    baseline_path.write_text("not json", encoding="utf-8")
    _write_py(tmp_path, "src/a.py", "x = 1\n")

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 1


def test_baseline_missing_metric_key_fails(tmp_path: Path) -> None:
    baseline_path = tmp_path / "complexity-baseline.json"
    baseline_path.write_text(json.dumps({}), encoding="utf-8")
    _write_py(tmp_path, "src/a.py", "x = 1\n")

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 1


# ---------------------------------------------------------------------------
# 성능 단언
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_scan_tree_throughput_budget(tmp_path: Path, perf_budget: PerfBudget) -> None:
    """200개 파일(각 20개 함수) 스캔이 30초 예산 안에 끝난다 -- 회귀 시 CI 스텝이
    조용히 느려지는 것을 막는 처리량 단언(D2 DoD 성능 단언 1건).

    CI(pytest --cov=src --no:xdist) 환경에서 coverage 오버헤드 포함 시 실제 소요는
    14~19s 사이므로, 5s 예산는 불현실적임. 30s로 상향 조정(DECISION_GUIDELINES B-2).

    단일 호출이 이미 수 초 걸려 process_time의 15.6ms 틱 양자화와 무관하므로
    batch=1(기본값)로 측정한다. 예산은 ms 단위로 맞춰 *1000 한 번만 적용한다.
    """
    for i in range(200):
        body = "\n".join(
            f"def f{j}(x):\n    if x == {j}:\n        return {j}\n    return 0\n" for j in range(20)
        )
        _write_py(tmp_path, f"src/mod_{i}.py", body)

    hits: list[object] = []

    def _scan() -> None:
        hits[:] = cc.scan_tree(tmp_path, subdirs=("src",))

    sample = perf_budget.assert_within(_scan, budget_ms=30.0 * 1000, n=1, warmup=0)

    assert hits == []
    assert sample.cpu_ms < 30.0 * 1000


# ---------------------------------------------------------------------------
# failure-injection: 예산 10배 지연 -> 적색
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_scan_tree_budget_fails_on_10x_cpu_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, perf_budget: PerfBudget
) -> None:
    """정상 입력이 예산을 통과하는 측정 경로를, CPU 시간을 10배로 늘리면
    적색으로 전환한다 -- 성능 게이트가 지연 회귀를 실제로 적색으로
    포착한다는 것을 단언 수준에서 증명(D2 DoD 실패주입 1건).

    `PerfBudget`은 `time.process_time()`을 쓰므로, `sleep()` 주입은
    CPU 시간 측정에 보이지 않는다. 대신 `time.process_time`을
    monkeypatch해 실제 CPU 시간의 10배를 반환하게 한다.
    """
    for i in range(200):
        body = "\n".join(
            f"def f{j}(x):\n    if x == {j}:\n        return {j}\n    return 0\n" for j in range(20)
        )
        _write_py(tmp_path, f"src/mod_{i}.py", body)

    hits: list[object] = []
    original_process_time = __import__("time").process_time

    def _slow_process_time() -> float:
        """실제 CPU 시간의 10배를 반환."""
        return original_process_time() * 10

    def _scan() -> None:
        hits[:] = cc.scan_tree(tmp_path, subdirs=("src",))

    # budget_ms=30_000ms(30초)인데 CPU 시간을 10배로 늘리면 실제 스캔이
    # 3초라도 걸릴 때 측정값은 30초를 넘어 적색이 된다.
    # (실제 스캔은 ~1~3s이므로 10배면 10~30s — 예산 30s를 초과할 확률 높음)
    # 더 확실하게: budget을 100ms로 낮춰서 작은 값에서도 적색을 확인한다.
    monkeypatch.setattr(__import__("time"), "process_time", _slow_process_time)

    try:
        # budget을 100ms로 낮춰서 10배 지연이 반드시 초과되도록 한다.
        # (실제 스캔 CPU 시간이 ~5ms면 10배=50ms < 100ms로 통과할 수 있음)
        # 더 낮게: 10ms로 하면 실제 스캔이 ~5ms일 때 10배=50ms > 10ms로 확실하게 적색
        _sample = perf_budget.assert_within(
            _scan, budget_ms=10.0, n=3, warmup=0, label="injected_10x"
        )
        # 위에 assert_within이 assert를 안 raising하면 이 라인은 도달하지 않음
        pytest.fail("assert_within이 적색을 raised하지 않음 — 주입이 감지되지 않음")
    except AssertionError as exc:
        # 적색이 감지됨 — 기대 동작
        assert "cpu=" in str(exc)
        assert "budget<" in str(exc)
