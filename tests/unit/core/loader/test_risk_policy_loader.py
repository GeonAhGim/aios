from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.core.loader.risk_policy_loader import (
    DEFAULT_RISK_POLICY_PATH,
    BundleMismatchError,
    RiskPolicy,
    load_risk_policy,
    verify_policy_against_bundle,
)
from src.core.risk.policy_bundle import BundleState, RiskRuleBundle, compute_rule_hash


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


# --- task-1194 R-21 §3.3 확장 ------------------------------------------------


def test_section_3_3_extension_fields_are_parsed():
    policy = load_risk_policy()

    assert policy.var.method == "cornish_fisher"
    assert policy.var.timeframe == "1d"
    assert policy.var.lookback_bars == 250
    assert policy.correlation_risk.lookback_bars == 90
    assert policy.correlation_risk.min_overlap == 30
    assert policy.correlation_risk.ewma_lambda is None
    assert policy.decision_ttl.pre_trade_sec == 1.0
    assert policy.decision_ttl.pre_submit_sec == 2.0
    assert policy.decision_ttl.deployment_sec == 10.0
    assert policy.reactivation.cooldown_sec == 300
    assert policy.reactivation.approval_ttl_sec == 1800
    assert policy.reactivation.evidence_required is True
    assert policy.liquidation.slice_count_min == 3
    assert policy.liquidation.slice_count_max == 20
    assert policy.data_distrust.min_sources == 3
    assert policy.data_distrust.quote_timeout_sec == 2


def test_var_min_bars_exceeding_lookback_bars_raises_validation_error():
    policy = load_risk_policy()
    raw = policy.model_dump()
    raw["var"]["min_bars"] = raw["var"]["lookback_bars"] + 1

    with pytest.raises(ValidationError):
        RiskPolicy(**raw)


def test_correlation_min_overlap_exceeding_lookback_bars_raises_validation_error():
    policy = load_risk_policy()
    raw = policy.model_dump()
    raw["correlation_risk"]["min_overlap"] = raw["correlation_risk"]["lookback_bars"] + 1

    with pytest.raises(ValidationError):
        RiskPolicy(**raw)


def test_correlation_ewma_lambda_out_of_unit_interval_raises_validation_error():
    policy = load_risk_policy()
    raw = policy.model_dump()
    raw["correlation_risk"]["ewma_lambda"] = 1.0

    with pytest.raises(ValidationError):
        RiskPolicy(**raw)


def test_liquidation_slice_count_min_exceeding_max_raises_validation_error():
    policy = load_risk_policy()
    raw = policy.model_dump()
    raw["liquidation"]["slice_count_min"] = raw["liquidation"]["slice_count_max"] + 1

    with pytest.raises(ValidationError):
        RiskPolicy(**raw)


def test_liquidation_interval_min_exceeding_max_raises_validation_error():
    policy = load_risk_policy()
    raw = policy.model_dump()
    raw["liquidation"]["interval_min_sec"] = raw["liquidation"]["interval_max_sec"] + 1

    with pytest.raises(ValidationError):
        RiskPolicy(**raw)


def test_liquidation_negative_max_slice_notional_raises_validation_error():
    policy = load_risk_policy()
    raw = policy.model_dump()
    raw["liquidation"]["max_slice_notional"] = -1.0

    with pytest.raises(ValidationError):
        RiskPolicy(**raw)


def test_data_distrust_exit_threshold_exceeding_enter_threshold_raises_validation_error():
    policy = load_risk_policy()
    raw = policy.model_dump()
    raw["data_distrust"]["exit_threshold_pct"] = raw["data_distrust"]["enter_threshold_pct"] + 0.1

    with pytest.raises(ValidationError):
        RiskPolicy(**raw)


def test_unknown_top_level_key_is_rejected():
    policy = load_risk_policy()
    raw = policy.model_dump()
    raw["unknown_block"] = {"x": 1}

    with pytest.raises(ValidationError):
        RiskPolicy(**raw)


def test_unknown_nested_key_is_rejected():
    policy = load_risk_policy()
    raw = policy.model_dump()
    raw["liquidation"]["unknown_field"] = 1.0

    with pytest.raises(ValidationError):
        RiskPolicy(**raw)


def test_missing_liquidation_block_is_rejected():
    policy = load_risk_policy()
    raw = policy.model_dump()
    del raw["liquidation"]

    with pytest.raises(ValidationError):
        RiskPolicy(**raw)


# --- verify_policy_against_bundle -------------------------------------------


def _sample_bundle(policy: RiskPolicy, engine_version: str, *, rule_hash: str | None = None):
    return RiskRuleBundle(
        id=uuid4(),
        version="v1",
        rule_hash=rule_hash if rule_hash is not None else compute_rule_hash(policy, engine_version),
        engine_version=engine_version,
        policy_snapshot={"version": policy.version},
        state=BundleState.ACTIVE,
        created_by=uuid4(),
        approved_by=uuid4(),
    )


def test_verify_policy_against_bundle_returns_none_when_hash_matches():
    policy = load_risk_policy()
    bundle = _sample_bundle(policy, "engine-v1")

    assert verify_policy_against_bundle(policy, bundle) is None


def test_verify_policy_against_bundle_raises_on_hash_mismatch():
    """§4.1 I6 — yaml hash가 ACTIVE 번들의 rule_hash와 다르면 DENY 취급돼야
    하므로, 이 불일치를 놓치지 않고 BundleMismatchError로 드러내야 한다."""
    policy = load_risk_policy()
    bundle = _sample_bundle(policy, "engine-v1", rule_hash="a" * 64)

    with pytest.raises(BundleMismatchError):
        verify_policy_against_bundle(policy, bundle)


def test_verify_policy_against_bundle_raises_when_engine_version_differs():
    policy = load_risk_policy()
    bundle = _sample_bundle(policy, "engine-v1")
    other_engine_bundle = RiskRuleBundle(
        **{**bundle.model_dump(), "engine_version": "engine-v2"}
    )

    with pytest.raises(BundleMismatchError):
        verify_policy_against_bundle(policy, other_engine_bundle)
