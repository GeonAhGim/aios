"""scripts/run_mutation.py 단위 테스트 -- task-2851 MUT-1.

cosmic-ray 프로세스를 실행하지 않는다(그건 통합 시나리오). 여기서는 결과 집계
(compute_score/classify_survivors), 연산자 필터 목록 생성, TOML 설정 문자열
생성, baseline 래칫(evaluate_ratchet) 같은 순수 함수만 검증한다 -- DB/네트워크
접근 없이 합성 WorkResult/WorkItem만으로 재현 가능해야 한다(coverage_ratchet.py
테스트와 동일한 원칙).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from cosmic_ray.work_item import (
    MutationSpec,
    WorkerOutcome,
    WorkItem,
    WorkResult,
)
from cosmic_ray.work_item import (
    TestOutcome as _TestOutcome,
)

from tests.support.perf_budget import PerfBudget

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


run_mutation = _load_module("run_mutation", SCRIPTS_DIR / "run_mutation.py")


def _spec(
    module_path: str = "src/foundation/ledger/domain/rounding.py",
    operator_name: str = "core/NumberReplacer",
    occurrence: int = 0,
    start_pos: tuple[int, int] = (10, 4),
    end_pos: tuple[int, int] = (10, 6),
    definition_name: str | None = "round_half_even",
) -> MutationSpec:
    return MutationSpec(
        module_path=module_path,
        operator_name=operator_name,
        occurrence=occurrence,
        start_pos=start_pos,
        end_pos=end_pos,
        definition_name=definition_name,
    )


def _killed() -> WorkResult:
    return WorkResult(worker_outcome=WorkerOutcome.NORMAL, test_outcome=_TestOutcome.KILLED)


def _survived(diff: str = "--- diff ---") -> WorkResult:
    return WorkResult(
        worker_outcome=WorkerOutcome.NORMAL, test_outcome=_TestOutcome.SURVIVED, diff=diff
    )


def _incompetent() -> WorkResult:
    return WorkResult(worker_outcome=WorkerOutcome.EXCEPTION, test_outcome=_TestOutcome.INCOMPETENT)


def _skipped() -> WorkResult:
    return WorkResult(worker_outcome=WorkerOutcome.SKIPPED, output="Filtered operator")


def _no_test() -> WorkResult:
    return WorkResult(worker_outcome=WorkerOutcome.NO_TEST)


# ---------------------------------------------------------------------------
# compute_score
# ---------------------------------------------------------------------------


def test_compute_score_empty_results_is_zero_without_division_error() -> None:
    score = run_mutation.compute_score([])

    assert score.total == 0
    assert score.score_percent == 0.0


def test_compute_score_classifies_all_four_buckets() -> None:
    results = [_killed(), _killed(), _survived(), _incompetent(), _skipped(), _no_test()]

    score = run_mutation.compute_score(results)

    assert (score.killed, score.survived, score.incompetent, score.skipped) == (2, 1, 1, 2)


def test_compute_score_excludes_incompetent_and_skipped_from_denominator() -> None:
    # 3 killed, 1 survived, 10 incompetent/skipped -- the noise must not dilute
    # the score (denominator is killed+survived only).
    results = (
        [_killed(), _killed(), _killed(), _survived()] + [_incompetent()] * 5 + [_skipped()] * 5
    )

    score = run_mutation.compute_score(results)

    assert score.scored_total == 4
    assert score.score_percent == 75.0


# ---------------------------------------------------------------------------
# exclude_operator_patterns
# ---------------------------------------------------------------------------

_ALL_OPERATORS = [
    "core/NumberReplacer",
    "core/ReplaceComparisonOperator_Lt_LtE",
    "core/ReplaceComparisonOperator_Lt_LtE_Extra",  # 접두어 함정: 진짜 다른 연산자
    "core/ReplaceBinaryOperator_Add_BitXor",
    "core/ExceptionReplacer",
]


def test_exclude_operator_patterns_omits_all_allowed_operators() -> None:
    excluded = run_mutation.exclude_operator_patterns(
        _ALL_OPERATORS, run_mutation.ALLOWED_OPERATORS
    )

    for name in _ALL_OPERATORS:
        if name in run_mutation.ALLOWED_OPERATORS:
            assert not any(__import__("re").fullmatch(p, name) for p in excluded), name


def test_exclude_operator_patterns_does_not_over_exclude_by_prefix() -> None:
    """`core/ReplaceComparisonOperator_Lt_LtE` is allowed; a distinct operator that
    merely starts with the same text must still be excluded (regression guard for
    an un-anchored regex that would accidentally allow it through, or worse,
    silently drop the real allowed operator via a partial match)."""
    excluded = run_mutation.exclude_operator_patterns(
        _ALL_OPERATORS, run_mutation.ALLOWED_OPERATORS
    )

    import re

    assert any(re.fullmatch(p, "core/ReplaceComparisonOperator_Lt_LtE_Extra") for p in excluded)
    assert not any(re.fullmatch(p, "core/ReplaceComparisonOperator_Lt_LtE") for p in excluded)


def test_exclude_operator_patterns_keeps_non_allowed_out() -> None:
    excluded = run_mutation.exclude_operator_patterns(
        _ALL_OPERATORS, run_mutation.ALLOWED_OPERATORS
    )

    import re

    assert any(re.fullmatch(p, "core/ExceptionReplacer") for p in excluded)
    assert any(re.fullmatch(p, "core/ReplaceBinaryOperator_Add_BitXor") for p in excluded)


# ---------------------------------------------------------------------------
# build_config_toml
# ---------------------------------------------------------------------------


def test_build_config_toml_uses_forward_slashes_only() -> None:
    domain = run_mutation.DOMAINS["oms"]
    text = run_mutation.build_config_toml(
        domain,
        module_path=Path("C:/aios/wt/backend-4/src/services/oms/domain"),
        python_exe=Path("C:/aios/mihwa-aios/.venv/Scripts/python.exe"),
        exclude_patterns=run_mutation.exclude_operator_patterns(
            ["core/ExceptionReplacer", "core/NumberReplacer"], run_mutation.ALLOWED_OPERATORS
        ),
    )

    assert "\\" not in text


def test_build_config_toml_includes_deselect_and_distributor() -> None:
    domain = run_mutation.DOMAINS["oms"]
    text = run_mutation.build_config_toml(
        domain,
        module_path=Path("src/services/oms/domain"),
        python_exe=Path("python"),
        exclude_patterns=[],
    )

    assert 'name = "local"' in text
    assert "--deselect" in text
    assert "test_pytest_gate_turns_red_when_remainder_absorption_is_removed" in text


def test_build_config_toml_empty_exclude_list_is_valid_empty_array() -> None:
    domain = run_mutation.DOMAINS["ledger"]
    text = run_mutation.build_config_toml(
        domain,
        module_path=Path("src/foundation/ledger/domain"),
        python_exe=Path("python"),
        exclude_patterns=[],
    )

    assert "exclude-operators = []" in text


# ---------------------------------------------------------------------------
# classify_survivors
# ---------------------------------------------------------------------------


def test_classify_survivors_only_includes_survived() -> None:
    items = [WorkItem.single("a", _spec()), WorkItem.single("b", _spec())]
    results = {"a": _survived(), "b": _killed()}

    survivors = run_mutation.classify_survivors(items, results)

    assert len(survivors) == 1
    assert survivors[0].module_path == "src/foundation/ledger/domain/rounding.py"


def test_classify_survivors_skips_missing_results() -> None:
    """negative: a work item with no matching result (still pending, or the DB
    write raced) must not crash classification."""
    items = [WorkItem.single("orphan", _spec())]

    survivors = run_mutation.classify_survivors(items, {})

    assert survivors == []


def test_classify_survivors_respects_limit_and_is_deterministically_sorted() -> None:
    items = []
    results = {}
    for i in range(5):
        job_id = f"job-{i}"
        items.append(WorkItem.single(job_id, _spec(start_pos=(50 - i, 0), end_pos=(50 - i, 2))))
        results[job_id] = _survived()

    survivors = run_mutation.classify_survivors(items, results, limit=3)

    assert len(survivors) == 3
    lines = [s.start_line for s in survivors]
    assert lines == sorted(lines)


# ---------------------------------------------------------------------------
# baseline read/write
# ---------------------------------------------------------------------------


def test_read_baseline_missing_file_returns_none(tmp_path: Path) -> None:
    assert run_mutation.read_baseline(tmp_path / "missing.json") is None


def test_read_baseline_malformed_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(run_mutation.MutationConfigError):
        run_mutation.read_baseline(path)


def test_read_baseline_non_numeric_value_raises(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text('{"ledger": "high"}', encoding="utf-8")

    with pytest.raises(run_mutation.MutationConfigError):
        run_mutation.read_baseline(path)


def test_read_baseline_not_an_object_raises(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(run_mutation.MutationConfigError):
        run_mutation.read_baseline(path)


def test_write_baseline_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    run_mutation.write_baseline(path, {"ledger": 92.3, "risk": 88.0})

    assert run_mutation.read_baseline(path) == {"ledger": 92.3, "risk": 88.0}


# ---------------------------------------------------------------------------
# evaluate_ratchet -- 게이트 판정 로직 (gate-red-repro 대상)
# ---------------------------------------------------------------------------


def test_evaluate_ratchet_first_run_seeds_baseline() -> None:
    result = run_mutation.evaluate_ratchet({"ledger": 91.0}, baseline=None)

    assert result.ok
    assert result.updated_baseline == {"ledger": 91.0}


def test_evaluate_ratchet_within_tolerance_passes_and_keeps_baseline() -> None:
    result = run_mutation.evaluate_ratchet(
        {"ledger": 90.6}, baseline={"ledger": 91.0}, tolerance=0.5
    )

    assert result.ok
    assert result.updated_baseline == {"ledger": 91.0}


def test_evaluate_ratchet_improvement_ratchets_baseline_up() -> None:
    result = run_mutation.evaluate_ratchet(
        {"ledger": 95.0}, baseline={"ledger": 91.0}, tolerance=0.5
    )

    assert result.ok
    assert result.updated_baseline == {"ledger": 95.0}


def test_evaluate_ratchet_domain_absent_from_current_keeps_recorded_baseline() -> None:
    """--domain ledger 처럼 일부 도메인만 도는 실행이 다른 도메인의 기록된
    baseline을 지우면 안 된다(부분 실행 = 이력 보존)."""
    result = run_mutation.evaluate_ratchet({"ledger": 92.0}, baseline={"ledger": 91.0, "oms": 88.0})

    assert result.updated_baseline["oms"] == 88.0


def test_evaluate_ratchet_regression_beyond_tolerance_fails_gate_red() -> None:
    """gate-red-repro: 실제 CI 단계(scripts/run_mutation.py)가 참조하는 판정
    함수가, 점수가 기준선 아래로 tolerance를 넘어 떨어지면 실제로 red(ok=False)가
    되는 것을 증명한다 -- 항상 통과만 하는 무의미한 게이트가 아님을 보인다."""
    result = run_mutation.evaluate_ratchet(
        {"ledger": 40.0}, baseline={"ledger": 91.0}, tolerance=0.5
    )

    assert result.ok is False
    assert any("FAIL" in msg for msg in result.messages)
    # 실패 시 baseline은 조용히 낮춰지지 않는다(coverage_ratchet.py와 동일 원칙).
    assert result.updated_baseline["ledger"] == 91.0


# ---------------------------------------------------------------------------
# failure injection: 테스트 스위트가 약해진(변이가 안 잡히는) 시나리오를
# compute_score가 그대로 낮은 점수로 반영하는지 -- 항상 초록만 내지 않는지 증명한다.
# ---------------------------------------------------------------------------


def test_compute_score_reflects_weakened_suite_regression() -> None:
    healthy_suite_results = [_killed()] * 18 + [_survived()] * 2  # 90%
    weakened_suite_results = [_killed()] * 4 + [
        _survived()
    ] * 16  # assertion silently removed -> 20%

    healthy_score = run_mutation.compute_score(healthy_suite_results)
    weakened_score = run_mutation.compute_score(weakened_suite_results)

    assert healthy_score.score_percent == 90.0
    assert weakened_score.score_percent == 20.0

    ratchet = run_mutation.evaluate_ratchet(
        {"risk": weakened_score.score_percent}, baseline={"risk": healthy_score.score_percent}
    )
    assert ratchet.ok is False


# ---------------------------------------------------------------------------
# perf assertion
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_compute_score_and_classify_survivors_scale_to_thousands_of_mutants(
    perf_budget: PerfBudget,
) -> None:
    """no accidental O(n^2): 5,000개 합성 결과에 대해 compute_score +
    classify_survivors가 500ms 안에 끝나야 한다(도메인 하나의 실측 변이 수는
    수백 건이지만, 여유를 두고 10배 규모로 검증한다).

    Raw `time.perf_counter()` → `perf_budget.sample(batch=N)` 전환 (task-15294):
    `process_time()` + coverage tracer 일시정지로 CI 병렬 호스트에서 양자화/
    계측 오버헤드를 제거. batch=100 으로 100회 호출을 묶어 호출당 평균 추정.
    예산 단위(ms) 일관성: perf_budget.sample().cpu_ms 는 ms 단위이므로
    예산 숫자(500)도 ms 기준 그대로 사용.

    주입 지연 적색 확인 (임시 실행): compute_score 에 10x 지연을 주입하면
    cpu_ms 가 500ms 를 크게 초과해 명확히 적색 — budget 단언이 실제 회귀를
    검출함을 입증. (원복 코드는 없음; 이 주석은 기록용.)
    """
    n = 5000
    items = []
    results = {}
    for i in range(n):
        job_id = f"job-{i}"
        items.append(WorkItem.single(job_id, _spec(start_pos=(i % 500, 0), end_pos=(i % 500, 2))))
        results[job_id] = _survived() if i % 3 == 0 else _killed()

    batch = 100

    score_sample = perf_budget.sample(
        lambda: run_mutation.compute_score(results.values()),
        batch=batch,
    )
    survivors_sample = perf_budget.sample(
        lambda: run_mutation.classify_survivors(items, results, limit=30),
        batch=batch,
    )

    assert score_sample.cpu_ms < 500, f"perf budget exceeded: {score_sample.cpu_ms:.1f}ms"
    assert survivors_sample.cpu_ms < 500, f"perf budget exceeded: {survivors_sample.cpu_ms:.1f}ms"


# ---------------------------------------------------------------------------
# 0-convergence + gate-red (task-11539 감사 후속)
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_compute_score_single_call_converges_to_near_zero_cpu(
    perf_budget: PerfBudget,
) -> None:
    """0 수렴 검증: compute_score 는 순수 Python 연산(사전에 빌드된 dict/values
    순회)만으로 구성되므로 단일 호출 CPU 시간이 0에 수렴해야 한다.
    `perf_budget.sample` 한 번의 cpu_ms 가 50ms 미만이면 충분하다.

    이 테스트는 audit(task-11539)가 "not dynamically measured"로标记한
    단일 호출 CPU 수렴을 처음으로 동적으로 측정한다.
    """
    n = 500
    results = {}
    for i in range(n):
        results[f"job-{i}"] = _killed() if i % 3 != 0 else _survived()

    sample = perf_budget.sample(
        lambda: run_mutation.compute_score(results.values()),
        batch=1,
    )

    assert sample.cpu_ms < 50, (
        f"single-call compute_score cpu_ms={sample.cpu_ms:.1f}ms — "
        f"pure-Python aggregation should converge to near 0"
    )


@pytest.mark.perf
def test_compute_score_cpu_budget_catches_10x_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    포착하는지 역으로 증명한다. `time.process_time`을 monkeypatch해 1회 호출당
    예산(500ms)을 초과하는 CPU 시간을 가장하면 budget 단언이
    AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다. fake process_time이 호출당 200ms를 더 반환하므로
    측정 예산 500ms 를 초과해 적색 확정.
    """
    _fake_time = [0.0]

    def _fake_process_time() -> float:
        _fake_time[0] += 200.0  # +200ms per call → 200ms >> 500ms budget for 5000 items
        return _fake_time[0]

    import tests.support.perf_budget

    monkeypatch.setattr(tests.support.perf_budget.time, "process_time", _fake_process_time)

    # 같은 측정/단언을 재사용 — budget 을 초과하므로 AssertionError 가 있어야 함
    n = 5000
    results = {}
    for i in range(n):
        results[f"job-{i}"] = _killed() if i % 3 != 0 else _survived()

    sample = perf_budget.sample(
        lambda: run_mutation.compute_score(results.values()),
        batch=1,
    )

    with pytest.raises(AssertionError):
        assert sample.cpu_ms < 500, f"perf budget exceeded: {sample.cpu_ms:.1f}ms"
