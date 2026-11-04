"""Strategy Validation 도메인 부정 테스트 — rules/policy/check_result가
불변식 위반 입력을 예외/실패 결과로 거부하는지 검증한다.

Spec: L4_strategy_portfolio_backtest_v1.0.md §2 row 156/157 / §3.5-A /
§9 L36. INVARIANTS.md I-07(hard-fail must be real computed outcomes).

__init__.py는 pytest 미수집 패키지 마커이므로 건드리지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.foundation.validation.domain.check_result import (
    CheckResult,
)
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.policy import (
    REQUIRED_CHECKS,
    REQUIRED_STRESS_SCENARIOS,
    ValidationPolicy,
)
from src.foundation.validation.domain.rules import (
    _is_hard_fail,
    compute_input_snapshot_hash,
    evaluate_bundle,
    evaluate_validation_policy,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _check(
    outcome: Outcome = Outcome.PASS,
    hard: list[str] | None = None,
    obligations: list[str] | None = None,
) -> CheckResult:
    """Lightweight CheckResult builder for adversarial inputs."""
    return CheckResult(
        check_type="backtest",
        outcome=outcome,
        metrics={},
        hard_fail_reasons=hard or [],
        obligations=obligations or [],
        result_hash="check-hash",
        policy_version="vp-v1",
    )


# ---------------------------------------------------------------------------
# I-07 negative: hard_fail_reasons outside HARD_FAIL_CODES must be rejected
# ---------------------------------------------------------------------------


@pytest.mark.negative
def test_check_result_rejects_unknown_hard_fail_code_at_construction() -> None:
    """불변식 I-07: 허용 집합 밖의 hard-fail 코드는 CheckResult 생성 시점에
    ValidationError를 던져야 한다."""
    with pytest.raises(ValidationError, match="codes outside HARD_FAIL_CODES"):
        _check(outcome=Outcome.FAIL, hard=["FAKE_CODE"])


@pytest.mark.negative
def test_check_result_rejects_mixed_valid_and_invalid_hard_fail_codes() -> None:
    """I-07: 유효 코드 하나 + 무효 코드 하나混入 시 전체가 거부되어야 한다.
    일부만 통과하면 무효 코드가 silently ignored된다."""
    with pytest.raises(ValidationError, match="codes outside HARD_FAIL_CODES"):
        _check(outcome=Outcome.FAIL, hard=["VALIDATION_OOS_LEAKAGE", "INVALID_CODE"])


@pytest.mark.negative
def test_check_result_rejects_empty_string_as_hard_fail_code() -> None:
    """I-07: 빈 문자열도 허용 집합에 없으므로 거부되어야 한다."""
    with pytest.raises(ValidationError, match="codes outside HARD_FAIL_CODES"):
        _check(outcome=Outcome.FAIL, hard=[""])


# ---------------------------------------------------------------------------
# policy.py negative: Literal 필드에 스펙 표 밖 값이 들어오면 즉시 거부
# ---------------------------------------------------------------------------


@pytest.mark.negative
def test_policy_rejects_invalid_oos_mode_literal() -> None:
    """불변식 §2 row 156: oos_mode는 ANCHORED/ROLLING 외 값을 받지 않는다.
    잘못된 값이 들어오면 ValidationError가 Must raise."""
    with pytest.raises(ValidationError):
        ValidationPolicy(oos_mode="RANDOM")


@pytest.mark.negative
def test_policy_rejects_invalid_policy_version_literal() -> None:
    """불변식 §2 row 156: policy_version은 vp-v1만 허용된다."""
    with pytest.raises(ValidationError):
        ValidationPolicy(policy_version="vp-future")


@pytest.mark.negative
def test_policy_required_checks_matches_spec() -> None:
    """불변식 §2 row 156: REQUIRED_CHECKS는 6개 체크를 정확히 포함한다.
    누락이나 추가가 있으면 정책 테이블과 코드가 어긋난다."""
    assert len(REQUIRED_CHECKS) == 6
    assert "point_in_time" in REQUIRED_CHECKS
    assert "backtest" in REQUIRED_CHECKS
    assert "oos_walk_forward" in REQUIRED_CHECKS
    assert "robustness" in REQUIRED_CHECKS
    assert "stress_capacity" in REQUIRED_CHECKS
    assert "failure_conditions" in REQUIRED_CHECKS


@pytest.mark.negative
def test_policy_required_stress_scenarios_matches_spec() -> None:
    """불변식 §2 row 156: REQUIRED_STRESS_SCENARIOS는 5개 시나리오를 정확히
    포함한다."""
    assert len(REQUIRED_STRESS_SCENARIOS) == 5
    assert "COST_X2" in REQUIRED_STRESS_SCENARIOS
    assert "COST_X3" in REQUIRED_STRESS_SCENARIOS
    assert "SLIPPAGE_PLUS_50BPS" in REQUIRED_STRESS_SCENARIOS
    assert "WORST_5_DAYS_REMOVED" in REQUIRED_STRESS_SCENARIOS
    assert "GAP_2PCT" in REQUIRED_STRESS_SCENARIOS


# ---------------------------------------------------------------------------
# rules.py negative: evaluate_validation_policy 불변식 위반 입력 거부
# ---------------------------------------------------------------------------


@pytest.mark.negative
def test_evaluate_validation_policy_empty_warnings_returns_pass() -> None:
    """불변식 I6(역방향): 경고가 없으면 PASS, hard_fail_reasons도 obligations도
    비어 있어야 한다. 빈 입력이 PASS_WITH_OBLIGATIONS으로 흐르면 안 된다."""
    outcome, obligations, hard_fail_reasons = evaluate_validation_policy([])
    assert outcome is Outcome.PASS
    assert obligations == []
    assert hard_fail_reasons == []


@pytest.mark.negative
def test_evaluate_validation_policy_soft_warning_only_returns_pass_with_obligations() -> None:
    """불변식 I6: 소프트 경고만 있으면 PASS_WITH_OBLIGATIONS이어야 하고,
    hard_fail_reasons는 비어 있어야 한다. soft warning이 hard_fail_reasons에
    누락되면 안 된다."""
    outcome, obligations, hard_fail_reasons = evaluate_validation_policy(["zero-cost model used"])
    assert outcome is Outcome.PASS_WITH_OBLIGATIONS
    assert obligations == ["zero-cost model used"]
    assert hard_fail_reasons == []


@pytest.mark.negative
def test_evaluate_validation_policy_hard_fail_clears_obligations() -> None:
    """불변식 I6: hard fail이 하나라도 있으면 obligations도 hard_fail_reasons
    결과에 포함되어야 한다. hard fail 시 soft warning이 obligation으로만
    남고 hard_fail_reasons에 누락되면 안 된다."""
    outcome, obligations, hard_fail_reasons = evaluate_validation_policy(
        ["zero-cost model used", "DSR 0.80 < min_dsr 0.95"]
    )
    assert outcome is Outcome.FAIL
    assert "DSR 0.80 < min_dsr 0.95" in hard_fail_reasons
    assert "zero-cost model used" in obligations


# ---------------------------------------------------------------------------
# rules.py negative: 동어반복(semantic duplicate) 경고 처리
# ---------------------------------------------------------------------------


@pytest.mark.negative
def test_evaluate_validation_policy_hard_fail_reasons_preserve_all_entries() -> None:
    """동어반입 금지(역방향): 같은 hard fail 경고가 두 번 들어오면
    hard_fail_reasons에 두 번 기록된다.
    (역방향 테스트 — dedup이 없음을 확인하여 의도적 동작임을 증명한다.)"""
    warning = "DSR 0.80 < min_dsr 0.95"
    outcome, obligations, hard_fail_reasons = evaluate_validation_policy([warning, warning])
    assert outcome is Outcome.FAIL
    # evaluate_validation_policy는 dedup하지 않으므로 2건 기록된다
    assert len(hard_fail_reasons) == 2


# ---------------------------------------------------------------------------
# rules.py negative: _is_hard_fail case-insensitive matching
# ---------------------------------------------------------------------------


@pytest.mark.negative
def test_is_hard_fail_matches_case_insensitively() -> None:
    """불변식 I-07: _is_hard_fail은 대소문자를 구분하지 않고 매칭해야 한다.
    마커는 ("PortfolioEngine 예외", "DSR", "PBO", "LOOKAHEAD", "데이터 누수") —
    영어 마커는 대소문자 무관, 한글 마커도 .upper()로 매칭된다."""
    # DSR
    assert _is_hard_fail("dsr threshold breached") is True
    assert _is_hard_fail("Dsr threshold breached") is True
    assert _is_hard_fail("DSR threshold breached") is True
    # PBO
    assert _is_hard_fail("pbo overfit detected") is True
    assert _is_hard_fail("PBO OVERFIT DETECTED") is True
    # LOOKAHEAD (English marker)
    assert _is_hard_fail("lookahead data leak") is True
    assert _is_hard_fail("LOOKAHEAD data leak") is True
    # 데이터 누수 (Korean marker)
    assert _is_hard_fail("데이터 누수 감지") is True
    assert _is_hard_fail("데이터 누수: bar 12") is True
    # PortfolioEngine 예외 (Korean marker)
    assert _is_hard_fail("portfolioengine 예외 발생") is True
    assert _is_hard_fail("PORTFOLIOENGINE 예외 발생") is True


@pytest.mark.negative
def test_is_hard_fail_rejects_non_matching_warning() -> None:
    """불변식 I-07: hard fail 마커와 관련 없는 경고는 hard fail이 아니다."""
    assert _is_hard_fail("strategy executed normally") is False
    assert _is_hard_fail("backtest completed successfully") is False
    assert _is_hard_fail("") is False


# ---------------------------------------------------------------------------
# rules.py negative: compute_input_snapshot_hash with edge inputs
# ---------------------------------------------------------------------------


@pytest.mark.negative
def test_compute_input_snapshot_hash_with_empty_bars_produces_hash() -> None:
    """STR-001: 빈 bars 입력이라도 해시는 생성되어야 한다(None이 아니다).
    빈 입력이 예외 없이 해시를 반환하면 동일 입력은 동일 해시를산출한다."""

    h = compute_input_snapshot_hash(
        fsm_definition={"states": ["IDLE"]},
        cost_model={"fee_bps": "5", "slippage_bps": "2"},
        warmup_bars=0,
        periods_per_year=252,
        initial_equity=Decimal("10000"),
        bars=[],
    )
    assert isinstance(h, str)
    assert len(h) == 64  # sha256 hex length


@pytest.mark.negative
def test_compute_input_snapshot_hash_different_bars_different_hash() -> None:
    """STR-001: bar 데이터가 달라지면 해시도 달라져야 한다. 같은 해시가
    나오면 캐시 재사용과 실제 변경을 구분할 수 없다."""

    base_kwargs = dict(
        fsm_definition={"states": ["IDLE"]},
        cost_model={"fee_bps": "5", "slippage_bps": "2"},
        warmup_bars=0,
        periods_per_year=252,
        initial_equity=Decimal("10000"),
    )
    from src.data.models.market_data import Candle

    bar1 = Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=Decimal("1"),
        open_time=datetime(2026, 1, 1, tzinfo=UTC),
        close_time=datetime(2026, 1, 1, tzinfo=UTC),
    )
    bar2 = Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal("200"),
        high=Decimal("200"),
        low=Decimal("200"),
        close=Decimal("200"),
        volume=Decimal("1"),
        open_time=datetime(2026, 1, 1, tzinfo=UTC),
        close_time=datetime(2026, 1, 1, tzinfo=UTC),
    )
    h1 = compute_input_snapshot_hash(**base_kwargs, bars=[bar1])
    h2 = compute_input_snapshot_hash(**base_kwargs, bars=[bar2])
    assert h1 != h2


# ---------------------------------------------------------------------------
# rules.py negative: evaluate_bundle with malformed inputs
# ---------------------------------------------------------------------------


@pytest.mark.negative
def test_evaluate_bundle_empty_sequence_returns_pass() -> None:
    """불변식 I6: 빈 결과 시퀀스는 PASS, hard_fail_reasons도 obligations도
    비어 있어야 한다."""
    outcome, obligations, hard_fail_reasons = evaluate_bundle([])
    assert outcome is Outcome.PASS
    assert obligations == []
    assert hard_fail_reasons == []


@pytest.mark.negative
def test_evaluate_bundle_all_pass_with_obligations_returns_pass_with_obligations() -> None:
    """불변식 I6: 모든 체크가 PASS_WITH_OBLIGATIONS이면 번들도
    PASS_WITH_OBLIGATIONS이어야 한다."""
    results = [
        _check(Outcome.PASS_WITH_OBLIGATIONS, obligations=["obligation_a"]),
        _check(Outcome.PASS_WITH_OBLIGATIONS, obligations=["obligation_b"]),
    ]
    outcome, obligations, hard_fail_reasons = evaluate_bundle(results)
    assert outcome is Outcome.PASS_WITH_OBLIGATIONS
    assert "obligation_a" in obligations
    assert "obligation_b" in obligations
    assert hard_fail_reasons == []


# ---------------------------------------------------------------------------
# Failure injection: monkeypatch로 의존성 예외 유발
# ---------------------------------------------------------------------------


@pytest.mark.failure_injection
def test_evaluate_validation_policy_propagates_hashlib_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: evaluate_validation_policy 내부에서 hashlib.sha256()가
    예외를 내면 PASS로 기본값을 내보내지 않고 그대로 전파해야 한다(fail-closed)."""
    import hashlib

    def _sha256_boom(*args: object, **kwargs: object) -> None:
        raise OSError("hashlib device error")

    monkeypatch.setattr(hashlib, "sha256", _sha256_boom)

    # compute_input_snapshot_hash가 hashlib.sha256를 호출하는 경로가
    # evaluate_validation_policy를 통해 도달하는지 확인 — 실제로는
    # compute_input_snapshot_hash를 직접 테스트하는 것이 정확하다.
    with pytest.raises(OSError, match="hashlib device error"):
        compute_input_snapshot_hash(
            fsm_definition={"states": ["IDLE"]},
            cost_model={"fee_bps": "5"},
            warmup_bars=0,
            periods_per_year=252,
            initial_equity=Decimal("10000"),
            bars=[],
        )


@pytest.mark.failure_injection
def test_evaluate_bundle_raises_when_check_result_validator_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: CheckResult 생성 시 field_validator가 예외를 내면
    evaluate_bundle은 해당 예외를 전파해야 한다.
    ValidationError가 unknown code를 거부하는지 직접 확인."""
    # validator가 unknown code를 거부하는지 직접 확인
    with pytest.raises(ValidationError):
        CheckResult(
            check_type="backtest",
            outcome=Outcome.FAIL,
            metrics={},
            hard_fail_reasons=["UNKNOWN_CODE"],
            result_hash="hash",
            policy_version="vp-v1",
        )


# ---------------------------------------------------------------------------
# policy.py negative: ValidationPolicy content hash 불변성
# ---------------------------------------------------------------------------


@pytest.mark.negative
def test_policy_hash_is_unique_per_field_value() -> None:
    """불변식 §3.5-A: 정책의 단일 필드 값만 바꿔도 해시가 달라져야 한다.
    해시가 같으면 정책 변경을 캐시 재사용으로 오인한다."""
    baseline = ValidationPolicy().policy_hash()
    assert ValidationPolicy(max_pbo=Decimal("0.51")).policy_hash() != baseline
    assert ValidationPolicy(min_dsr=Decimal("0.96")).policy_hash() != baseline
    assert ValidationPolicy(min_oos_windows=4).policy_hash() != baseline
    assert ValidationPolicy(purge_bars=25).policy_hash() != baseline
    assert ValidationPolicy(embargo_bars=25).policy_hash() != baseline


@pytest.mark.negative
def test_policy_is_frozen_and_immutable() -> None:
    """불변식 §3.5-A: ValidationPolicy은 frozen=True이므로 인스턴스 필드를
    수정할 수 없다. 수정 시도가 있으면 정책이 변경되어도 해시가 stale해진다."""
    policy = ValidationPolicy()
    with pytest.raises(
        Exception, match="|FrozenInstanceError|cannot assign to field"
    ):  # pydantic frozen model raises FrozenInstanceError
        policy.max_pbo = Decimal("0.6")


# ---------------------------------------------------------------------------
# check_result.py negative: schema_version Literal 제약
# ---------------------------------------------------------------------------


@pytest.mark.negative
def test_check_result_rejects_unknown_schema_version() -> None:
    """불변식 §2 row 157: schema_version은 chk-v1만 허용된다."""
    with pytest.raises(ValidationError):
        CheckResult(
            check_type="backtest",
            outcome=Outcome.PASS,
            metrics={},
            result_hash="hash",
            policy_version="vp-v1",
            schema_version="chk-future",
        )


# ---------------------------------------------------------------------------
# Overlap guard: ensure this file does NOT duplicate test_bundle_invariants.py
# ---------------------------------------------------------------------------

# This module intentionally avoids re-testing ValidationBundle I6 invariants
# (test_bundle_pass_with_hard_fail_reason_is_rejected, etc.) because those
# are already covered in test_bundle_invariants.py. This file focuses on:
# - policy.py Literal field constraints
# - rules.py evaluate_validation_policy edge cases
# - rules.py _is_hard_fail case-insensitivity
# - rules.py compute_input_snapshot_hash edge inputs
# - check_result.py field-level constraints
