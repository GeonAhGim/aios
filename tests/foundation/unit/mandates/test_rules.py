"""75번 §2/§3 규칙의 단위테스트 — DB 없이 순수 함수만 검증한다."""
from datetime import datetime, timezone
from uuid import uuid4

from src.foundation.mandates.domain.models import (
    Autonomy,
    MandateRevision,
    MandateRevisionState,
    PolicyEvaluationSubject,
    PolicyOutcome,
)
from src.foundation.mandates.domain.rules import (
    compute_revision_hash,
    detect_material_change,
    evaluate_policy,
)

NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _revision(**overrides: object) -> MandateRevision:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        mandate_id=uuid4(),
        revision_no=1,
        state=MandateRevisionState.ACTIVE,
        max_total_exposure_pct=80.0,
        max_single_instrument_pct=20.0,
        min_cash_buffer_pct=5.0,
        max_daily_loss_pct=3.0,
        allowed_autonomy=Autonomy.PAPER,
        forbidden_assets=("XYZ",),
    )
    defaults.update(overrides)
    return MandateRevision(**defaults)  # type: ignore[arg-type]


def test_revision_hash_is_stable_for_same_rule_content():
    a = _revision(id=uuid4())
    b = _revision(id=uuid4())  # id만 다름
    assert compute_revision_hash(a) == compute_revision_hash(b)


def test_revision_hash_changes_when_a_rule_field_changes():
    a = _revision()
    b = _revision(max_total_exposure_pct=90.0)
    assert compute_revision_hash(a) != compute_revision_hash(b)


def test_evaluate_policy_allows_within_all_limits():
    revision = _revision()
    subject = PolicyEvaluationSubject(
        command_type="paper_deployment",
        total_exposure_pct=50.0,
        instrument_exposure_pct=10.0,
        cash_buffer_pct=10.0,
        projected_daily_loss_pct=1.0,
        requested_autonomy=Autonomy.PAPER,
        asset="BTC/USDT",
    )
    outcome, reasons, obligations = evaluate_policy(revision, subject)
    assert outcome == PolicyOutcome.ALLOW
    assert reasons == []
    assert obligations == ["REQUIRE_RISK_GATE"]


def test_evaluate_policy_denies_total_exposure_breach():
    revision = _revision()
    subject = PolicyEvaluationSubject(command_type="x", total_exposure_pct=95.0)
    outcome, reasons, _ = evaluate_policy(revision, subject)
    assert outcome == PolicyOutcome.DENY
    assert "POLICY_MAX_TOTAL_EXPOSURE" in reasons


def test_evaluate_policy_denies_forbidden_asset():
    revision = _revision()
    subject = PolicyEvaluationSubject(command_type="x", asset="XYZ")
    outcome, reasons, _ = evaluate_policy(revision, subject)
    assert outcome == PolicyOutcome.DENY
    assert reasons == ["POLICY_FORBIDDEN_ASSET"]


def test_evaluate_policy_denies_autonomy_exceeded():
    revision = _revision(allowed_autonomy=Autonomy.OBSERVE)
    subject = PolicyEvaluationSubject(command_type="x", requested_autonomy=Autonomy.PAPER)
    outcome, reasons, _ = evaluate_policy(revision, subject)
    assert outcome == PolicyOutcome.DENY
    assert reasons == ["POLICY_AUTONOMY_EXCEEDED"]


def test_evaluate_policy_pause_required_overrides_deny_when_daily_loss_breached():
    """일일 손실 한도 초과는 다른 위반과 동시에 발생해도 PAUSE_REQUIRED가
    우선한다(더 강한 조치)."""
    revision = _revision()
    subject = PolicyEvaluationSubject(
        command_type="x",
        total_exposure_pct=95.0,  # 이것도 위반
        projected_daily_loss_pct=5.0,  # 이것도 위반, max=3.0
    )
    outcome, reasons, _ = evaluate_policy(revision, subject)
    assert outcome == PolicyOutcome.PAUSE_REQUIRED
    assert "POLICY_MAX_TOTAL_EXPOSURE" in reasons
    assert "POLICY_MAX_DAILY_LOSS" in reasons


def test_evaluate_policy_ignores_unset_subject_fields():
    """subject의 필드가 None이면(아직 모르는 값) 그 규칙은 평가하지 않는다 —
    "모른다"를 "위반 아님"으로 오판하지 않되, 위반으로도 임의 판단하지 않음."""
    revision = _revision()
    subject = PolicyEvaluationSubject(command_type="x")
    outcome, reasons, _ = evaluate_policy(revision, subject)
    assert outcome == PolicyOutcome.ALLOW
    assert reasons == []


def test_detect_material_change_flags_increased_exposure():
    current = _revision(max_total_exposure_pct=80.0)
    proposed = _revision(max_total_exposure_pct=90.0)
    assert "MATERIAL_MAX_TOTAL_EXPOSURE_INCREASED" in detect_material_change(current, proposed)


def test_detect_material_change_flags_reduced_cash_buffer():
    current = _revision(min_cash_buffer_pct=10.0)
    proposed = _revision(min_cash_buffer_pct=5.0)
    assert "MATERIAL_CASH_BUFFER_REDUCED" in detect_material_change(current, proposed)


def test_detect_material_change_flags_autonomy_elevation():
    current = _revision(allowed_autonomy=Autonomy.OBSERVE)
    proposed = _revision(allowed_autonomy=Autonomy.LIMITED_LIVE)
    assert "MATERIAL_AUTONOMY_ELEVATED" in detect_material_change(current, proposed)


def test_detect_material_change_flags_universe_expansion():
    current = _revision(forbidden_assets=("XYZ", "ABC"))
    proposed = _revision(forbidden_assets=("XYZ",))  # ABC가 더 이상 금지되지 않음
    assert "MATERIAL_UNIVERSE_EXPANDED" in detect_material_change(current, proposed)


def test_detect_material_change_is_empty_when_strictly_more_conservative():
    """더 보수적으로만 바뀌면(위험 축소) material change가 아니다 — 사용자가
    스스로 위험을 줄이는 데 MFA 재인증/cooling-off를 강제할 이유가 없다."""
    current = _revision()
    proposed = _revision(
        max_total_exposure_pct=50.0,  # 축소
        max_single_instrument_pct=10.0,  # 축소
        max_daily_loss_pct=1.0,  # 축소
        min_cash_buffer_pct=20.0,  # 확대(더 보수적)
        allowed_autonomy=Autonomy.OBSERVE,  # 하향
        forbidden_assets=("XYZ", "ABC"),  # 확대(더 제한적)
    )
    assert detect_material_change(current, proposed) == []
