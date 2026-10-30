"""L36 -- unit tests for `ValidationPolicy` (§2 row 156) and `CheckResult`
(§2 row 157). Pure domain types, no DB. Includes D2 evidence (ADR-2026-09-09-C
Decision 1): negative >=3, failure injection 1, perf assertion 1, gate-red
repro 1.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.check_import_linter import ROOT as LINTER_ROOT
from scripts.check_import_linter import _eval_forbidden_suffix, _imports_of, parse_contracts
from src.foundation.validation.domain.check_result import HARD_FAIL_CODES, CheckResult
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.policy import ValidationPolicy
from src.foundation.validation.domain.rules import evaluate_validation_policy
from tests.support.coverage_pause import paused_coverage
from tests.support.perf_budget import PerfBudget

_DOMAIN_DIR = Path(__file__).resolve().parents[4] / "src" / "foundation" / "validation" / "domain"
_DOMAIN_FILES = ("policy", "check_result", "artifact")


def test_policy_defaults_match_spec_table():
    policy = ValidationPolicy()
    assert policy.policy_version == "vp-v1"
    assert policy.min_oos_windows == 3
    assert policy.max_pbo == Decimal("0.5")
    assert policy.min_dsr == Decimal("0.95")
    assert policy.allow_zero_cost is False
    assert policy.required_stress == (
        "COST_X2",
        "COST_X3",
        "SLIPPAGE_PLUS_50BPS",
        "WORST_5_DAYS_REMOVED",
        "GAP_2PCT",
    )
    assert policy.max_param_isolation == Decimal("0.5")
    assert policy.required_checks == (
        "point_in_time",
        "backtest",
        "oos_walk_forward",
        "robustness",
        "stress_capacity",
        "failure_conditions",
    )


def test_policy_hash_is_stable_for_identical_policy():
    a = ValidationPolicy().policy_hash()
    b = ValidationPolicy().policy_hash()
    assert a == b


def test_policy_hash_changes_when_max_pbo_changes():
    baseline = ValidationPolicy().policy_hash()
    changed = ValidationPolicy(max_pbo=Decimal("0.51")).policy_hash()
    assert baseline != changed


def test_check_result_rejects_hard_fail_code_outside_closed_set():
    with pytest.raises(ValidationError):
        CheckResult(
            check_type="point_in_time",
            outcome=Outcome.FAIL,
            metrics={},
            hard_fail_reasons=["NOT_A_REAL_CODE"],
            result_hash="deadbeef",
            policy_version="vp-v1",
        )


def test_check_result_accepts_known_hard_fail_code():
    code = next(iter(HARD_FAIL_CODES))
    result = CheckResult(
        check_type="point_in_time",
        outcome=Outcome.FAIL,
        metrics={},
        hard_fail_reasons=[code],
        result_hash="deadbeef",
        policy_version="vp-v1",
    )
    assert result.hard_fail_reasons == [code]


# --------------------------------------------------------------------------
# D2 negative -- Literal 필드에 스펙 표 밖 값이 들어오면 즉시 거부(§2 row 156/157)
# --------------------------------------------------------------------------


def test_policy_rejects_invalid_oos_mode():
    with pytest.raises(ValidationError):
        ValidationPolicy(oos_mode="INVALID")


def test_policy_rejects_invalid_policy_version():
    with pytest.raises(ValidationError):
        ValidationPolicy(policy_version="vp-v2")


def test_check_result_rejects_invalid_schema_version():
    with pytest.raises(ValidationError):
        CheckResult(
            check_type="point_in_time",
            outcome=Outcome.PASS,
            metrics={},
            result_hash="deadbeef",
            policy_version="vp-v1",
            schema_version="chk-v2",
        )


def test_policy_is_frozen_rejects_mutation():
    """§3.5-A reproducibility -- a policy is pinned at construction; a mutated
    field could silently drift a bundle's judged-against policy away from what
    `policy_hash()` still reports."""
    policy = ValidationPolicy()
    with pytest.raises(ValidationError):
        policy.max_pbo = Decimal("0.6")


# --------------------------------------------------------------------------
# D2 failure injection -- rules.evaluate_validation_policy와 CheckResult의
# 실제 배선. rules.py의 hard_fail_reasons는 자유 형식 경고 텍스트이지
# HARD_FAIL_CODES의 닫힌 코드 집합이 아니다(check_result.py docstring이 명시).
# application 계층의 코드-매핑 단계가 빠진 회귀(경고 텍스트를 그대로 넘김)를
# 시뮬레이션해 field_validator가 조용히 통과시키지 않고 fail-closed 거부함을
# 증명한다 -- 두 모듈이 실제로 같은 계약 경계에서 만난다는 증거.
# --------------------------------------------------------------------------


def test_check_result_rejects_raw_rules_warning_text_as_hard_fail_code():
    outcome, obligations, hard_fail_reasons = evaluate_validation_policy(
        ["PortfolioEngine 예외: division by zero"]
    )
    assert outcome == Outcome.FAIL
    assert hard_fail_reasons == ["PortfolioEngine 예외: division by zero"]
    with pytest.raises(ValidationError):
        CheckResult(
            check_type="backtest",
            outcome=outcome,
            metrics={},
            hard_fail_reasons=hard_fail_reasons,
            result_hash="deadbeef",
            policy_version="vp-v1",
        )


# --------------------------------------------------------------------------
# D2 성능 단언 -- ADR-2026-09-09-C 예산표 "DSL 컴파일 300ms"를 이 leaf에 적용.
# policy_hash()는 매 검증 번들 평가마다(§3.5-A) 호출되는 컴파일-인접 단계이므로
# 1,000회 반복이 그 예산 안에서 끝남을 확인한다(canonical_json 정규화 회귀 방지).
# --------------------------------------------------------------------------


@pytest.mark.perf
def test_policy_hash_throughput_within_dsl_compile_budget(perf_budget):
    policy = ValidationPolicy()
    perf_budget.assert_within(
        lambda: policy.policy_hash(),
        budget_ms=300,
        batch=1000,
        label="1000x policy_hash()",
    )


# --------------------------------------------------------------------------
# D2 single-call CPU convergence -- 1회 호출이 0에 수렴하는 CPU 시간을 보여야
# 한다. time.process_time()은 Windows에서 약 15.6ms 양자화 오차가 있으므로
# batch=64로 여러 호출을 묶어서 1회당 오차를 줄인다.
# --------------------------------------------------------------------------


@pytest.mark.perf
def test_policy_hash_cpu_converges_to_zero(perf_budget):
    """단일 policy_hash() 생성이 0에 수렴하는 CPU 시간을 보여야 한다.

    `time.process_time()`은 Windows에서 약 15.6ms 양자화 오차가 있으므로,
    best_of(여러 번 중 최소)로 측정해 양자화 tick 1회에 흔들리지 않게 한다.
    1회당 CPU 시간이 1ms 미만이어야 순수 도메인 로직이라는 불변식(I-03)과
    호환된다.
    """
    policy = ValidationPolicy()

    def _one_call() -> str:
        return policy.policy_hash()

    # best_of: 여러 샘플 중 최소 CPU 값 — Windows 양자화 tick 노이즈 제거
    sample = perf_budget.best_of(_one_call, n=3, warmup=1, batch=5)

    assert sample.cpu_ms < 1.0, (
        f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
        f"Pydantic 모델 직렬화+해시는 1ms 미만이어야 함 (budget<1ms)"
    )


# --------------------------------------------------------------------------
# D2 gate-red reproduction -- 10x 지연 monkeypatch가预算을 초과하면
# AssertionError로 적색이 됨을 역으로 증명한다.
# --------------------------------------------------------------------------


def test_policy_hash_budget_catches_10x_cpu_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    포착하는지 역으로 증명한다. `virtual_delayed_sync`로 CPU 가상 지연(예산
    10배인 10ms)을 주입하면 budget 단언이 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    import tests.support.perf_inject as _pi_module

    _pi_module.reset_virtual_totals()

    policy = ValidationPolicy()

    def _build_once() -> str:
        # 10ms 가상 CPU 지연 주입 — 실제 busy-wait/sleep 없이 ledger만 증가
        _pi_module.virtual_delayed_sync(policy.policy_hash, 0.01)()
        return policy.policy_hash()

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    # best_of로 Windows 양자화 tick 노이즈 제거 (CPU 예산 테스트이므로 batch=5)
    with pytest.raises(AssertionError):
        sample = perf_budget.best_of(_build_once, n=3, warmup=1, batch=5)
        assert sample.cpu_ms < 1.0, (
            f"지연 주입 후 CPU 시간 {sample.cpu_ms:.3f}ms — "
            f"budget 단언이 적색을 포착해야 함 (budget<1ms)"
        )


# --------------------------------------------------------------------------
# D2 failure injection -- time.sleep 주입은 CPU 측정에 영향을 주지 않아야
# 한다. PerfBudget이 time.process_time()을 사용하므로 sleep은 wall-clock만
# 늘리고 CPU 시간에는 영향을 주지 않는다.
# --------------------------------------------------------------------------


def test_policy_hash_sleep_injection_does_not_affect_cpu_measurement(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트-red 재현 — `policy_hash()` 생성은 순수 CPU 작업이므로
    `time.sleep`을 monkeypatch해도 CPU 시간이 늘어나지 않는다. PerfBudget이
    `time.process_time()`을 사용하므로 sleep 주입은 CPU 측정에 영향을
    주지 않는다.

    sleep이 CPU 측정에 영향을 준다면 PerfBudget의 측정 방식이 잘못됐다.

    CI 병행 실행에서 CPU 경합 spike를 피하기 위해 best_of로
    best-of-3 최소값을 사용한다 (task-19444, task-19391 패턴).
    """
    _sleep_calls = [0]
    import tests.support.perf_budget as _pb_module

    _real_sleep = _pb_module.time.sleep

    def _fake_sleep(seconds: float) -> None:
        _sleep_calls[0] += 1
        # 100ms sleep × 10회 = 1초 wall-clock 대기
        _real_sleep(0.1)

    monkeypatch.setattr(_pb_module.time, "sleep", _fake_sleep)

    policy = ValidationPolicy()

    def _build_once() -> str:
        # sleep을 호출하도록 유도하지만 policy_hash()는 sleep을 쓰지 않음
        _fake_sleep(0)
        return policy.policy_hash()

    # CI의 --cov=src line-tracer 오버헤드로 policy_hash()가 1ms를 넘을 수 있다.
    # paused_coverage()로 coverage를 완전히 멈춘 뒤 CPU 측정 구간을 감싼다.
    with paused_coverage():
        # best_of: 3회 샘플 중 CPU 최소값 — CI 경합 노이즈 제거
        sample = perf_budget.best_of(_build_once, n=3, warmup=1, batch=5)

        # sleep 주입에도 CPU 시간이 1ms 미만이어야 함
        assert sample.cpu_ms < 1.0, (
            f"sleep 주입 후 CPU 시간 {sample.cpu_ms:.3f}ms — "
            f"sleep은 CPU 측정에 영향을 주지 않아야 함 (budget<1ms)"
        )


# --------------------------------------------------------------------------
# D2 게이트 적색 재현 -- .importlinter domain-no-adapters 계약이 이 leaf
# (policy.py/check_result.py/artifact.py)의 순수성(도메인이 같은 애그리게잇의
# adapters/를 임포트할 수 없음)의 실제 CI 집행자임을, 합성 회귀 그래프가
# 적색으로 잡히는지와 실제 현재 import는 녹색인지 대조 증명한다.
# --------------------------------------------------------------------------


def test_import_linter_domain_no_adapters_catches_validation_domain_regression():
    contracts = parse_contracts(LINTER_ROOT / ".importlinter")
    domain_no_adapters = next(c for c in contracts if c["id"] == "domain-no-adapters")

    regressed_graph = {
        "src.foundation.validation.domain.policy": {
            "src.foundation.validation.adapters.postgres_repository"
        }
    }
    hits = _eval_forbidden_suffix(regressed_graph, domain_no_adapters)
    assert len(hits) == 1
    assert hits[0][0] == "src.foundation.validation.domain.policy"

    real_graph = {
        f"src.foundation.validation.domain.{name}": _imports_of(
            _DOMAIN_DIR / f"{name}.py",
            f"src.foundation.validation.domain.{name}",
            False,
        )
        for name in _DOMAIN_FILES
    }
    assert _eval_forbidden_suffix(real_graph, domain_no_adapters) == []
