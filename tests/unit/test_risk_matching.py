"""15.3 단위테스트 — 순수 비교 로직."""
import pytest

from src.services.risk_matching import RiskMatchingError, check_mismatch


def test_matching_or_lower_risk_target_produces_no_warning():
    assert check_mismatch("공격형", "안정형") is None
    assert check_mismatch("중립형", "중립형") is None


def test_higher_risk_target_produces_warning():
    warning = check_mismatch("안정형", "공격형")
    assert warning is not None
    assert "안정형" in warning
    assert "공격형" in warning


def test_none_target_risk_level_skips_warning():
    assert check_mismatch("안정형", None) is None


def test_unknown_user_profile_raises():
    with pytest.raises(RiskMatchingError):
        check_mismatch("모름", "공격형")


def test_unknown_target_risk_level_raises():
    with pytest.raises(RiskMatchingError):
        check_mismatch("안정형", "모름")
