"""L4_risk_and_safety_v1.0.md#9 R-15 — `policy_bundle.py` 단위 테스트.

번들 상태값 노트(R-22 전제와의 정합): 이 테스트는 `RiskRuleBundle`이
표현하는 상태 전이(DRAFT→APPROVED→ACTIVE→RETIRED)가 78번 §1
`risk_rule_bundle`의 partial unique(`ux_bundle_active`, scope당 ACTIVE
최대 1개)·conditional 전이 전제와 모순되지 않음을 보인다 — 이 모델은
scope 간 비교나 DB 원자성을 다루지 않고 "단일 번들 인스턴스가 그 상태로
유효한가"만 순수 판정하므로, R-22 어댑터가 `conditional_update`로 실제
원자적 전이를 수행하기 전 단계의 사전 검증으로만 쓰인다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.risk.policy_bundle import (
    BundleState,
    RiskRuleBundle,
    compute_rule_hash,
    is_valid_transition,
)

_ENGINE_VERSION = "engine-v1"

_YAML_TEMPLATE = """\
version: "draft-1"

daily_loss:
  warning_pct: {daily_loss_warning}
  halt_pct: 5.0

max_drawdown:
  warning_pct: 10.0
  hard_stop_pct: 15.0

leverage:
  default_max: 3.0
  coverage_multiplier:
    high: 1.0
    medium: 0.7
    low: 0.5

position_concentration:
  single_asset_max_pct: 20.0

strategy_allocation:
  unverified_max_pct: 10.0
  certified_level4_max_pct: 25.0

var:
  confidence: 0.95
  horizon_days: 1
  max_pct: 5.0

correlation_risk:
  threshold: 0.7
  aggregate_exposure_max_pct: 30.0

trade_frequency:
  anomaly_multiplier: 3.0

circuit_breaker:
  warning:
    api_error_rate_pct: 10.0
    data_delay_sec: 2.0
  restricted:
    api_error_rate_pct: 25.0
    order_reject_rate_pct: 15.0
  halted:
    data_delay_sec: 5.0
  emergency:
    daily_loss_pct: 5.0
    api_disconnect_sec: 30.0

watchdog:
  loss_threshold_pct: 7.0
  unresponsive_sec: 30
  window_min: 5

data_distrust:
  enter_threshold_pct: 1.5
  exit_threshold_pct: 0.75
  exit_sustain_sec: 60

execution_loop:
  interval_sec: 1.0
"""

# 위와 논리적으로 동일하지만 주석이 곳곳에 추가되고 최상위 키 순서가
# 뒤바뀐 변형 — yaml 파싱 후 dict 내용은 같아야 한다.
_YAML_REORDERED_WITH_COMMENTS = """\
# 최상위 키 순서를 의도적으로 흩트린 변형본
execution_loop:
  interval_sec: 1.0  # 실행 루프 폴링 주기, 리스크 수치 아님

data_distrust:
  exit_sustain_sec: 60
  enter_threshold_pct: 1.5
  exit_threshold_pct: 0.75

watchdog:
  window_min: 5
  loss_threshold_pct: 7.0
  unresponsive_sec: 30

circuit_breaker:
  emergency:
    api_disconnect_sec: 30.0
    daily_loss_pct: 5.0
  halted:
    data_delay_sec: 5.0
  restricted:
    order_reject_rate_pct: 15.0
    api_error_rate_pct: 25.0
  warning:
    data_delay_sec: 2.0
    api_error_rate_pct: 10.0

trade_frequency:
  anomaly_multiplier: 3.0  # 24시간 대비 배수

correlation_risk:
  aggregate_exposure_max_pct: 30.0
  threshold: 0.7

var:
  max_pct: 5.0
  horizon_days: 1
  confidence: 0.95

strategy_allocation:
  certified_level4_max_pct: 25.0
  unverified_max_pct: 10.0

position_concentration:
  single_asset_max_pct: 20.0

leverage:
  coverage_multiplier:
    low: 0.5
    medium: 0.7
    high: 1.0
  default_max: 3.0

max_drawdown:
  hard_stop_pct: 15.0
  warning_pct: 10.0

# 이 주석은 draft-1 정책이 아직 인간 승인 전임을 알리는 운영 메모다
daily_loss:
  halt_pct: 5.0
  warning_pct: {daily_loss_warning}

version: "draft-1"  # 정책 스키마 버전이 아니라 정책 자체의 버전
"""


def _write_yaml(tmp_path, name, text, *, daily_loss_warning="3.0"):
    path = tmp_path / name
    path.write_text(text.format(daily_loss_warning=daily_loss_warning), encoding="utf-8")
    return path


def _sample_bundle(**overrides):
    fields = {
        "id": uuid4(),
        "version": "v1",
        "rule_hash": "a" * 64,
        "engine_version": _ENGINE_VERSION,
        "policy_snapshot": {"version": "draft-1"},
        "state": BundleState.DRAFT,
        "created_by": uuid4(),
    }
    fields.update(overrides)
    return RiskRuleBundle(**fields)


def test_same_policy_and_engine_version_hash_is_stable(tmp_path):
    path = _write_yaml(tmp_path, "a.yaml", _YAML_TEMPLATE)
    policy = load_risk_policy(path)
    assert compute_rule_hash(policy, _ENGINE_VERSION) == compute_rule_hash(
        policy, _ENGINE_VERSION
    )


def test_yaml_comments_and_key_order_do_not_change_hash(tmp_path):
    canonical = load_risk_policy(_write_yaml(tmp_path, "canonical.yaml", _YAML_TEMPLATE))
    reordered = load_risk_policy(
        _write_yaml(tmp_path, "reordered.yaml", _YAML_REORDERED_WITH_COMMENTS)
    )
    assert compute_rule_hash(canonical, _ENGINE_VERSION) == compute_rule_hash(
        reordered, _ENGINE_VERSION
    )


def test_numeric_value_change_changes_hash(tmp_path):
    baseline = load_risk_policy(_write_yaml(tmp_path, "baseline.yaml", _YAML_TEMPLATE))
    changed = load_risk_policy(
        _write_yaml(tmp_path, "changed.yaml", _YAML_TEMPLATE, daily_loss_warning="3.1")
    )
    assert compute_rule_hash(baseline, _ENGINE_VERSION) != compute_rule_hash(
        changed, _ENGINE_VERSION
    )


def test_engine_version_change_changes_hash(tmp_path):
    policy = load_risk_policy(_write_yaml(tmp_path, "a.yaml", _YAML_TEMPLATE))
    assert compute_rule_hash(policy, "engine-v1") != compute_rule_hash(policy, "engine-v2")


def test_valid_transition_sequence_is_allowed():
    assert is_valid_transition(BundleState.DRAFT, BundleState.APPROVED)
    assert is_valid_transition(BundleState.APPROVED, BundleState.ACTIVE)
    assert is_valid_transition(BundleState.ACTIVE, BundleState.RETIRED)


def test_skipping_or_reversing_states_is_rejected():
    assert not is_valid_transition(BundleState.DRAFT, BundleState.ACTIVE)
    assert not is_valid_transition(BundleState.APPROVED, BundleState.DRAFT)
    assert not is_valid_transition(BundleState.RETIRED, BundleState.ACTIVE)
    assert not is_valid_transition(BundleState.RETIRED, BundleState.DRAFT)


def test_draft_bundle_without_approver_is_valid():
    bundle = _sample_bundle(state=BundleState.DRAFT, approved_by=None)
    assert bundle.state == BundleState.DRAFT
    assert bundle.approved_by is None


def test_approved_bundle_without_approver_denies_fail_closed():
    with pytest.raises(ValidationError):
        _sample_bundle(state=BundleState.APPROVED, approved_by=None)


def test_active_bundle_without_approver_denies_fail_closed():
    with pytest.raises(ValidationError):
        _sample_bundle(state=BundleState.ACTIVE, approved_by=None)


def test_invalid_rule_hash_length_rejected():
    with pytest.raises(ValidationError):
        _sample_bundle(rule_hash="deadbeef")


def test_invalid_rule_hash_non_hex_rejected():
    with pytest.raises(ValidationError):
        _sample_bundle(rule_hash="z" * 64)


def test_naive_datetime_rejected_for_effective_from():
    with pytest.raises(ValidationError):
        _sample_bundle(
            state=BundleState.APPROVED,
            approved_by=uuid4(),
            effective_from=datetime(2026, 9, 4, 0, 0),
        )


def test_aware_utc_datetime_accepted_for_effective_from():
    bundle = _sample_bundle(
        state=BundleState.APPROVED,
        approved_by=uuid4(),
        effective_from=datetime(2026, 9, 4, 0, 0, tzinfo=timezone.utc),
    )
    assert bundle.effective_from is not None
    assert bundle.effective_from.tzinfo is not None
