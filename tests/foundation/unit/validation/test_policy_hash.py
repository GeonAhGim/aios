"""L36 -- unit tests for `ValidationPolicy` (§2 row 156) and `CheckResult`
(§2 row 157). Pure domain types, no DB.
"""
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.foundation.validation.domain.check_result import HARD_FAIL_CODES, CheckResult
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.policy import ValidationPolicy


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
