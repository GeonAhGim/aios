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

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any
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
  es_max_pct: 7.0
  min_bars: 60
  method: "cornish_fisher"
  timeframe: "1d"
  lookback_bars: 250

correlation_risk:
  threshold: 0.7
  aggregate_exposure_max_pct: 30.0
  lookback_bars: 90
  min_overlap: 30
  ewma_lambda: null

trade_frequency:
  anomaly_multiplier: 3.0
  max_trades_per_hour: 60

decision_ttl:
  pre_trade_sec: 1.0
  pre_submit_sec: 2.0
  deployment_sec: 10.0

reactivation:
  cooldown_sec: 300
  approval_ttl_sec: 1800
  evidence_required: true

liquidation:
  max_participation_pct: 10.0
  slice_count_min: 3
  slice_count_max: 20
  size_jitter_pct: 30.0
  interval_min_sec: 2
  interval_max_sec: 15
  max_slice_notional: 5000
  limit_tolerance_bps: 15
  slice_ttl_sec: 5
  adverse_move_abort_pct: 1.0
  total_deadline_sec: 300

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
  min_sources: 3
  quote_timeout_sec: 2

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
  quote_timeout_sec: 2
  min_sources: 3
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
  max_trades_per_hour: 60

liquidation:
  total_deadline_sec: 300
  adverse_move_abort_pct: 1.0
  slice_ttl_sec: 5
  limit_tolerance_bps: 15
  max_slice_notional: 5000
  interval_max_sec: 15
  interval_min_sec: 2
  size_jitter_pct: 30.0
  slice_count_max: 20
  slice_count_min: 3
  max_participation_pct: 10.0

reactivation:
  evidence_required: true
  approval_ttl_sec: 1800
  cooldown_sec: 300

decision_ttl:
  deployment_sec: 10.0
  pre_submit_sec: 2.0
  pre_trade_sec: 1.0

correlation_risk:
  ewma_lambda: null
  min_overlap: 30
  lookback_bars: 90
  aggregate_exposure_max_pct: 30.0
  threshold: 0.7

var:
  lookback_bars: 250
  timeframe: "1d"
  method: "cornish_fisher"
  max_pct: 5.0
  horizon_days: 1
  confidence: 0.95
  es_max_pct: 7.0
  min_bars: 60

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


def test_active_to_approved_rollback_is_rejected():
    # DRAFT로의 역행뿐 아니라 ACTIVE에서 APPROVED로의 "부분 롤백"도 선형
    # 전이 밖이다 — 공격자가 재승인 없이 이전 단계로 되돌려 새 정책을
    # 밀어넣는 경로를 막는다.
    assert not is_valid_transition(BundleState.ACTIVE, BundleState.APPROVED)


def test_uppercase_hex_rule_hash_rejected_as_bypass_attempt():
    # sha256_hex는 항상 소문자 hex만 만든다. 대문자 hex는 바이트 값은
    # 유효해 보이지만 이 스키마가 강제하는 정규형이 아니다 — 대소문자를
    # 섞어 문자열 비교(rule_hash == bundle.rule_hash)를 우회하려는 시도를
    # 검증 단계에서 그대로 막는다(fail-closed).
    with pytest.raises(ValidationError):
        _sample_bundle(rule_hash="A" * 64)


class _CorruptedPolicy:
    """`RiskPolicy`를 흉내 내지만 직렬화 시점에 실패하는 적대적 이중체 —
    부패했거나 조작된 정책 객체가 로더 계층을 뚫고 들어온 상황을 흉내낸다."""

    def model_dump(self, mode: str) -> dict[str, Any]:
        raise RuntimeError("corrupted policy object: model_dump failed")


def test_model_dump_failure_propagates_instead_of_silent_fallback_hash():
    # I6(rule_hash 불일치 시 전체 DENY)가 의미를 가지려면, 해시 계산 자체가
    # 실패했을 때 조용히 빈/기본 해시를 반환해서는 안 된다 — 예외가 그대로
    # 전파돼 호출부가 이를 "해시 불일치"가 아니라 "계산 실패"로 fail-closed
    # 처리하게 강제한다.
    with pytest.raises(RuntimeError):
        compute_rule_hash(_CorruptedPolicy(), _ENGINE_VERSION)  # type: ignore[arg-type]


def test_compute_rule_hash_repeated_calls_stay_bounded(tmp_path):
    # canonical_json의 정규화가 정책 크기에 대해 병적으로(지수적으로) 느려
    # 지지 않는지 — 200회 반복이 여유 있는 상한 내에 끝나야 한다. 리스크
    # 게이트 경로(R-16 evaluate)에서 매 결정마다 호출되므로 성능 회귀는
    # 곧 지연 회귀다.
    policy = load_risk_policy(_write_yaml(tmp_path, "perf.yaml", _YAML_TEMPLATE))
    start = time.perf_counter()
    for _ in range(200):
        compute_rule_hash(policy, _ENGINE_VERSION)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0


def test_multiple_concurrent_engine_instances_derive_identical_rule_hash(tmp_path):
    # 다중 인스턴스 증명: 실제 배치 환경에서는 여러 봇/엔진 프로세스가 각자
    # 독립적으로 risk_policy.yaml을 로드해 rule_hash를 계산한다.
    # `verify_policy_against_bundle`(I6)이 fleet 전체에서 일관되게
    # DENY/ALLOW를 판정하려면, 이 독립 계산들이 항상 같은 해시로 수렴해야
    # 한다 — 스레드 동시 실행으로 이를 흉내내 증명한다.
    path = _write_yaml(tmp_path, "fleet.yaml", _YAML_TEMPLATE)

    def _load_and_hash(_: int) -> str:
        policy = load_risk_policy(path)
        return compute_rule_hash(policy, _ENGINE_VERSION)

    with ThreadPoolExecutor(max_workers=8) as pool:
        hashes = list(pool.map(_load_and_hash, range(8)))

    assert len(hashes) == 8
    assert len(set(hashes)) == 1
