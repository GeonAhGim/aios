from pathlib import Path

import pytest
from pydantic import ValidationError

from src.core.loader.risk_policy_loader import (
    DEFAULT_RISK_POLICY_PATH,
    RiskPolicy,
    load_risk_policy,
)


def test_load_default_risk_policy_file():
    policy = load_risk_policy()

    assert policy.version == "draft-1"
    assert policy.daily_loss.warning_pct == 3.0
    assert policy.data_distrust.enter_threshold_pct == 1.5
    assert policy.circuit_breaker.emergency.daily_loss_pct == 5.0


def test_default_path_points_to_actual_file():
    assert DEFAULT_RISK_POLICY_PATH.exists()
    assert DEFAULT_RISK_POLICY_PATH.name == "risk_policy.yaml"


def test_schema_violation_raises_validation_error(tmp_path: Path):
    broken = tmp_path / "broken_risk_policy.yaml"
    broken.write_text("version: draft-1\ndaily_loss:\n  warning_pct: 3.0\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        load_risk_policy(broken)


def test_out_of_range_percentage_raises_validation_error():
    """docs/RED_TEAM_FINDINGS.md #14 회귀 — 타입만 맞으면 범위를 벗어난
    값(음수 임계값, 100% 초과 상한 등)도 그대로 통과해 실제 운영 정책이
    될 수 있었다."""
    policy = load_risk_policy()
    raw = policy.model_dump()
    raw["daily_loss"]["halt_pct"] = -5.0  # 음수 손실 임계값 — 의미 없음

    with pytest.raises(ValidationError):
        RiskPolicy(**raw)


def test_position_concentration_over_100_pct_raises_validation_error():
    policy = load_risk_policy()
    raw = policy.model_dump()
    raw["position_concentration"]["single_asset_max_pct"] = 150.0

    with pytest.raises(ValidationError):
        RiskPolicy(**raw)


def test_var_confidence_out_of_unit_interval_raises_validation_error():
    policy = load_risk_policy()
    raw = policy.model_dump()
    raw["var"]["confidence"] = 1.5

    with pytest.raises(ValidationError):
        RiskPolicy(**raw)
