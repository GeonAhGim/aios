"""L36 -- unit tests for `ValidationPolicy` (§2 row 156) and `CheckResult`
(§2 row 157). Pure domain types, no DB. Includes D2 evidence (ADR-2026-09-09-C
Decision 1): negative >=3, failure injection 1, perf assertion 1, gate-red
repro 1.
"""

import time
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


def test_policy_hash_throughput_within_dsl_compile_budget():
    policy = ValidationPolicy()
    start = time.perf_counter()
    for _ in range(1000):
        policy.policy_hash()
    elapsed = time.perf_counter() - start
    assert elapsed < 0.3, f"1000x policy_hash() took {elapsed * 1000:.2f}ms, budget 300ms"


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
