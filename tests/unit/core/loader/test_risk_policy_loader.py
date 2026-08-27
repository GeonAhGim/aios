from pathlib import Path

import pytest
from pydantic import ValidationError

from src.core.loader.risk_policy_loader import DEFAULT_RISK_POLICY_PATH, load_risk_policy


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
