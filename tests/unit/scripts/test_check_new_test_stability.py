"""scripts/check_new_test_stability.py: 새 테스트 파일을 반복 실행해 flaky를 적발한다.

subprocess는 주입해서 실제 프로세스 없이 검증한다."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_new_test_stability", ROOT / "scripts" / "check_new_test_stability.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_passes_when_every_repeat_passes_and_varies_the_hash_seed() -> None:
    m = _load()
    seen: list[tuple[list[str], dict[str, str]]] = []

    def run(cmd, env, cwd, timeout):
        seen.append((cmd, env))
        return 0

    assert m.check(["tests/unit/test_a.py"], [], run=run, repeats=3) == []
    assert [e["PYTHONHASHSEED"] for _, e in seen] == ["1", "2", "3"]
    assert all("test_a.py" in c[-1] for c, _ in seen)


def test_a_failure_in_any_single_repeat_is_reported() -> None:
    m = _load()
    results = iter([0, 1, 0])

    problems = m.check(["tests/unit/test_a.py"], [], run=lambda *a: next(results), repeats=3)

    assert len(problems) == 1 and "2/3" in problems[0] and "rc=1" in problems[0]


def test_vitest_files_run_shuffled_and_skip_when_node_is_missing() -> None:
    m = _load()
    cmds: list[list[str]] = []

    def run(cmd, env, cwd, timeout):
        cmds.append(cmd)
        return 0

    m.check([], ["src/x.test.ts"], run=run, repeats=2, have_node=True)
    assert len(cmds) == 2 and all(
        "--sequence.shuffle" in c and c[-1] == "src/x.test.ts" for c in cmds
    )
    cmds.clear()
    assert m.check([], ["src/x.test.ts"], run=run, repeats=2, have_node=False) == []
    assert cmds == []  # node_modules가 없으면 검증 불가 -- 통과


def test_exhausted_budget_or_timeout_stops_verifying_without_failing() -> None:
    m = _load()
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    calls: list[int] = []

    def run(cmd, env, cwd, timeout):
        calls.append(1)
        return 0

    out = m.check(
        ["tests/unit/test_a.py"], [], run=run, repeats=3, budget_sec=10, clock=lambda: next(ticks)
    )
    assert out == [] and len(calls) == 1  # 첫 반복만 돌고 예산이 소진돼 멈춘다
    assert (
        m.check(["tests/unit/test_a.py"], [], run=lambda *a: 124, repeats=3) == []
    )  # 시간 초과 = 검증 불가


def test_no_added_test_files_is_a_pass() -> None:
    m = _load()
    assert m.main(["--base", "HEAD"]) == 0
