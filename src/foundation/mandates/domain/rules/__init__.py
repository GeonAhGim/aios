"""Portfolio Mandate 순수 규칙 함수 — DB/HTTP 없이 단위 테스트 가능해야 한다.

Spec: AIOSproject 75_portfolio_mandate_l3_build_and_operational_specification_v1.0.md §2/§3.
"""
from __future__ import annotations

import hashlib

from src.foundation.mandates.domain.models import (
    Autonomy,
    MandateRevision,
    PolicyEvaluationSubject,
    PolicyOutcome,
)

_AUTONOMY_ORDER = (Autonomy.OBSERVE, Autonomy.PAPER, Autonomy.LIMITED_LIVE)

_COMPILER_VERSION = "v1"


def compute_revision_hash(revision: MandateRevision) -> str:
    """75번 §1 "immutable" + MAN-001 "stable hash". 규칙에 영향을 주는 필드만
    해시에 넣는다 — id/timestamp 등 메타데이터는 제외해 같은 규칙 내용이면
    항상 같은 해시가 나오게 한다(재현성)."""
    payload = "|".join(
        [
            f"{revision.max_total_exposure_pct:.2f}",
            f"{revision.max_single_instrument_pct:.2f}",
            f"{revision.min_cash_buffer_pct:.2f}",
            f"{revision.max_daily_loss_pct:.2f}",
            revision.allowed_autonomy.value,
            ",".join(sorted(revision.forbidden_assets)),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compile_rule_hash(revision: MandateRevision) -> str:
    """PolicyCompiler.compile()의 결정론적 산출물 — 지금은 규칙이 revision
    필드 그대로라 revision_hash와 같은 알고리즘을 쓰지만, 컴파일러 버전이
    바뀌면(예: 규칙 정규화 방식 변경) 이 함수만 바뀌고 revision_hash는
    그대로 둔다(둘의 관심사가 다르다 — 75번 §1 `policy_bundle.rule_hash`는
    "컴파일 결과"이지 "원본 입력"이 아니다)."""
    return compute_revision_hash(revision)


def compiler_version() -> str:
    return _COMPILER_VERSION


def detect_material_change(current: MandateRevision, proposed: MandateRevision) -> list[str]:
    """75번 §2 material change 6종 중 지금 모델이 표현 가능한 4종을 검출한다
    (leverage/short, jurisdiction/tax, delegated actor는 이 리프의 필드에
    아직 없음 — 45번 §1 스콥 축소와 동일 원칙). 순서는 심각도 무관, 발견된
    전부를 반환한다."""
    reasons: list[str] = []
    if proposed.max_total_exposure_pct > current.max_total_exposure_pct:
        reasons.append("MATERIAL_MAX_TOTAL_EXPOSURE_INCREASED")
    if proposed.max_single_instrument_pct > current.max_single_instrument_pct:
        reasons.append("MATERIAL_MAX_SINGLE_INSTRUMENT_INCREASED")
    if proposed.max_daily_loss_pct > current.max_daily_loss_pct:
        reasons.append("MATERIAL_MAX_DAILY_LOSS_INCREASED")
    if proposed.min_cash_buffer_pct < current.min_cash_buffer_pct:
        reasons.append("MATERIAL_CASH_BUFFER_REDUCED")
    if _AUTONOMY_ORDER.index(proposed.allowed_autonomy) > _AUTONOMY_ORDER.index(
        current.allowed_autonomy
    ):
        reasons.append("MATERIAL_AUTONOMY_ELEVATED")
    if set(current.forbidden_assets) - set(proposed.forbidden_assets):
        reasons.append("MATERIAL_UNIVERSE_EXPANDED")
    return reasons


def evaluate_policy(
    revision: MandateRevision, subject: PolicyEvaluationSubject
) -> tuple[PolicyOutcome, list[str], list[str]]:
    """75번 §3 EvaluatePolicy — (outcome, reason_codes, obligations). 규칙은
    발견 즉시 반환하지 않고 전부 모은다(72번 §4 taxonomy: 사용자가 한 번에
    여러 위반 사유를 봐야 재시도 왕복이 줄어든다). 결과가 DENY/PAUSE_REQUIRED
    여러 개면 가장 강한 것 하나로 outcome을 정한다(PAUSE_REQUIRED > DENY)."""
    reasons: list[str] = []
    pause_required = False

    if subject.total_exposure_pct is not None and (
        subject.total_exposure_pct > revision.max_total_exposure_pct
    ):
        reasons.append("POLICY_MAX_TOTAL_EXPOSURE")
    if subject.instrument_exposure_pct is not None and (
        subject.instrument_exposure_pct > revision.max_single_instrument_pct
    ):
        reasons.append("POLICY_MAX_SINGLE_INSTRUMENT")
    if subject.cash_buffer_pct is not None and (
        subject.cash_buffer_pct < revision.min_cash_buffer_pct
    ):
        reasons.append("POLICY_MIN_CASH_BUFFER")
    if subject.asset is not None and subject.asset in revision.forbidden_assets:
        reasons.append("POLICY_FORBIDDEN_ASSET")
    if subject.requested_autonomy is not None and (
        _AUTONOMY_ORDER.index(subject.requested_autonomy)
        > _AUTONOMY_ORDER.index(revision.allowed_autonomy)
    ):
        reasons.append("POLICY_AUTONOMY_EXCEEDED")
    if subject.projected_daily_loss_pct is not None and (
        subject.projected_daily_loss_pct > revision.max_daily_loss_pct
    ):
        reasons.append("POLICY_MAX_DAILY_LOSS")
        pause_required = True

    if pause_required:
        outcome = PolicyOutcome.PAUSE_REQUIRED
    elif reasons:
        outcome = PolicyOutcome.DENY
    else:
        outcome = PolicyOutcome.ALLOW

    # 45번 §1 "ALLOW는 risk gate의 승인이나 provider 실행 성공을 뜻하지
    # 않는다" — ALLOW를 포함해 항상 risk gate를 거치라는 의무를 명시적으로
    # 남긴다(75번 §3 예시 출력의 obligations와 동일 패턴).
    obligations = ["REQUIRE_RISK_GATE"]
    return outcome, reasons, obligations
