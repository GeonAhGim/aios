"""UX-14 `follow/application/mirror_signal.py` — 게이트 통과 적대적 테스트.

ADR-2026-09-09-C D2 증빙:
  - negative >= 3: 도메인 거부(listing 불일치·비중 초과·최소 단위 미달·
    paper_only 계약 위반) + 게이트 거부(risk/compliance) 다수.
  - 실패 주입 1: risk_gate 저장소가 쓰기 실패하면 예외가 삼켜지지 않고
    그대로 전파된다(fail-closed, `test_risk_gate_storage_failure_propagates`).
  - 성능 단언 1: `test_repeated_mirror_calls_stay_within_budget` — 왕복
    수(게이트 호출 1:1) 정확 단언 + 절대 지연 상한(비차단 print, task-1521
    선례와 동일하게 환경 정규화 상한 사용).
  - 게이트 적색 재현 1: `test_compliance_gate_denies_via_real_restricted_list_rule`
    — 스텁이 아니라 CM-8 실제 `restricted_list` 규칙 코드 경로로 DENY를
    낸다.

전부 인메모리 fake로 실행한다(follow leaf 자체는 DB를 쓰지 않는다 — spec
§2.4 파일 표에 follow 전용 adapters/migrations가 없다, risk_gate/mandates의
실제 저장소 I/O는 그쪽 leaf의 책임/증빙 범위).
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from src.foundation.follow.application.mirror_signal import (
    MirrorGateDeniedError,
    mirror_signal,
)
from src.foundation.follow.contracts.v1 import FollowSubscription, SizingPolicy, SourceSignal
from src.foundation.follow.domain.mirror_rules import MirrorRejectedError, MirrorRejectionReason
from src.foundation.risk_gate.domain.models import SafetyControl, SafetyControlState, SafetyScope
from tests.foundation.unit.follow._mirror_signal_fakes import (
    NOW,
    FakeMandateRepository,
    FakeRiskGateRepository,
    UnusedConnectionRepository,
)

SOURCE_LISTING = uuid4()
SYMBOL = "BTC-USD"


def _subscription(**overrides: Any) -> FollowSubscription:
    base: dict[str, Any] = dict(
        id=uuid4(),
        follower_tenant_id=uuid4(),
        follower_portfolio=uuid4(),
        source_listing=SOURCE_LISTING,
        sizing_policy=SizingPolicy.MIRROR_WEIGHT,
        max_notional=Decimal("1000.00"),
    )
    base.update(overrides)
    return FollowSubscription(**base)


def _signal(**overrides: Any) -> SourceSignal:
    base: dict[str, Any] = dict(
        source_listing=SOURCE_LISTING,
        symbol=SYMBOL,
        side="BUY",
        source_weight_pct=Decimal("50"),
        is_active=True,
    )
    base.update(overrides)
    return SourceSignal(**base)


# ---- 계약 불변 (negative) --------------------------------------------------


def test_paper_only_invariant_rejected_at_contract_construction() -> None:
    """UX-A3 paper_only 불변 — 구독 생성 시점에서 막는다, 애플리케이션
    호출까지 갈 필요조차 없다."""
    with pytest.raises(ValueError, match="FOLLOW_LIVE_NOT_AUTHORIZED"):
        _subscription(paper_only=False)


def test_non_positive_max_notional_rejected_at_contract_construction() -> None:
    with pytest.raises(ValueError, match="FOLLOW_MAX_NOTIONAL_INVALID"):
        _subscription(max_notional=Decimal("0"))


# ---- 도메인 거부 조건 (negative, 게이트를 부르기 전에 끝난다) --------------


async def test_listing_mismatch_rejected_before_any_gate_call() -> None:
    subscription = _subscription()
    signal = _signal(source_listing=uuid4())
    risk_repo, mandate_repo, connection_repo = (
        FakeRiskGateRepository(),
        FakeMandateRepository(),
        UnusedConnectionRepository(),
    )

    with pytest.raises(MirrorRejectedError) as exc_info:
        await mirror_signal(
            risk_repo,
            mandate_repo,
            connection_repo,
            subscription=subscription,
            signal=signal,
            now=NOW,
        )

    assert exc_info.value.reason == MirrorRejectionReason.LISTING_MISMATCH
    assert risk_repo.list_active_controls_calls == 0
    assert mandate_repo.get_mandate_calls == 0


async def test_max_notional_exceeded_rejected() -> None:
    """악의적/버그성 상류 입력(source_weight_pct > 100)이 max_notional을
    넘는 환산을 만들면 게이트 이전에 거부한다."""
    subscription = _subscription(max_notional=Decimal("1000.00"))
    signal = _signal(source_weight_pct=Decimal("150"))
    risk_repo, mandate_repo, connection_repo = (
        FakeRiskGateRepository(),
        FakeMandateRepository(),
        UnusedConnectionRepository(),
    )

    with pytest.raises(MirrorRejectedError) as exc_info:
        await mirror_signal(
            risk_repo,
            mandate_repo,
            connection_repo,
            subscription=subscription,
            signal=signal,
            now=NOW,
        )

    assert exc_info.value.reason == MirrorRejectionReason.MAX_NOTIONAL_EXCEEDED
    assert risk_repo.list_active_controls_calls == 0


async def test_below_minimum_unit_rejected_as_zero_quantity() -> None:
    subscription = _subscription(max_notional=Decimal("1000.00"))
    signal = _signal(source_weight_pct=Decimal("0.0001"))  # 1000*0.0001/100 = 0.001 < 0.01
    risk_repo, mandate_repo, connection_repo = (
        FakeRiskGateRepository(),
        FakeMandateRepository(),
        UnusedConnectionRepository(),
    )

    with pytest.raises(MirrorRejectedError) as exc_info:
        await mirror_signal(
            risk_repo,
            mandate_repo,
            connection_repo,
            subscription=subscription,
            signal=signal,
            now=NOW,
        )

    assert exc_info.value.reason == MirrorRejectionReason.ZERO_QUANTITY


# ---- 게이트 거부 (negative + 게이트 적색 재현) ------------------------------


async def test_risk_gate_denial_blocks_before_compliance_is_ever_reached() -> None:
    """UX-A3 — 팔로워 자신의 tenant에 걸린 kill switch(SafetyControl)가 진짜
    DENY를 낸다(스텁 outcome이 아니라 `evaluate_risk`의 실제 우선순위 트리
    경로). compliance 단계는 아예 도달하지 않는다."""
    subscription = _subscription()
    signal = _signal()
    active_control = SafetyControl(
        id=uuid4(),
        scope=SafetyScope.TENANT,
        scope_ref=str(subscription.follower_tenant_id),
        state=SafetyControlState.ACTIVE,
        reason="test kill switch",
        actor_subject_id=uuid4(),
        fence_token=1,
    )
    risk_repo = FakeRiskGateRepository(active_controls=(active_control,))
    mandate_repo = FakeMandateRepository()
    connection_repo = UnusedConnectionRepository()

    with pytest.raises(MirrorGateDeniedError) as exc_info:
        await mirror_signal(
            risk_repo,
            mandate_repo,
            connection_repo,
            subscription=subscription,
            signal=signal,
            now=NOW,
        )

    assert exc_info.value.gate == "risk"
    assert any("RISK_KILL_SWITCH_ACTIVE" in code for code in exc_info.value.reason_codes)
    assert "PRE_TRADE_COMPLIANCE" not in mandate_repo.policy_decision_command_types


async def test_compliance_gate_denies_via_real_restricted_list_rule() -> None:
    """게이트 적색 재현(D2) — mandate의 `forbidden_assets`에 이 신호의 symbol을
    넣어, CM-8 `evaluate_pre_trade`가 실제 `restricted_list` 규칙 코드 경로로
    DENY를 낸다(가짜로 DENY를 리턴하게 만든 스텁이 아니다). risk gate는
    ALLOW라서 여기까지 도달한다."""
    subscription = _subscription()
    signal = _signal()
    risk_repo = FakeRiskGateRepository()
    mandate_repo = FakeMandateRepository(forbidden_assets=(SYMBOL,))
    connection_repo = UnusedConnectionRepository()

    with pytest.raises(MirrorGateDeniedError) as exc_info:
        await mirror_signal(
            risk_repo,
            mandate_repo,
            connection_repo,
            subscription=subscription,
            signal=signal,
            now=NOW,
        )

    assert exc_info.value.gate == "compliance"
    assert "restricted_list" in exc_info.value.reason_codes
    assert "PRE_TRADE_COMPLIANCE" in mandate_repo.policy_decision_command_types


# ---- 성공 경로 --------------------------------------------------------------


async def test_successful_mirror_produces_paper_only_intent() -> None:
    subscription = _subscription(max_notional=Decimal("1000.00"))
    signal = _signal(source_weight_pct=Decimal("50"))
    risk_repo, mandate_repo, connection_repo = (
        FakeRiskGateRepository(),
        FakeMandateRepository(),
        UnusedConnectionRepository(),
    )

    intent = await mirror_signal(
        risk_repo,
        mandate_repo,
        connection_repo,
        subscription=subscription,
        signal=signal,
        now=NOW,
    )

    assert intent.paper_only is True
    assert intent.subscription_id == subscription.id
    assert intent.draft.notional == Decimal("500.00")
    assert intent.draft.side == "BUY"
    assert intent.compliance_decision_id is not None


# ---- 실패 주입 --------------------------------------------------------------


class _StorageFailure(RuntimeError):
    pass


async def test_risk_gate_storage_failure_propagates_fail_closed() -> None:
    """실패 주입(D2) — risk_gate 저장소 쓰기가 실패하면 예외를 삼키지 않고
    그대로 전파해야 한다. 삼켜서 ALLOW로 위장하면 UX-A3(팔로워 자신의 게이트
    통과)가 무의미해진다."""
    subscription = _subscription()
    signal = _signal()
    risk_repo = FakeRiskGateRepository()
    risk_repo.fail_insert_evaluation = _StorageFailure("simulated risk_gate storage failure")
    mandate_repo = FakeMandateRepository()
    connection_repo = UnusedConnectionRepository()

    with pytest.raises(_StorageFailure, match="simulated risk_gate storage failure"):
        await mirror_signal(
            risk_repo,
            mandate_repo,
            connection_repo,
            subscription=subscription,
            signal=signal,
            now=NOW,
        )


# ---- 성능 단언 ---------------------------------------------------------------

_CALL_COUNT = 200
_LATENCY_CEILING_SECONDS = 2.0
"""인메모리 fake만 쓰므로(DB I/O 0), ADR-2026-09-09-C §5 "사전거래 게이트
p99 5ms"(실 DB 기준) 예산을 그대로 가져다 쓸 수는 없다 — 대신 왕복(게이트
호출) 수를 정확히 1:1로 단언하고, 절대 지연은 task-1521 선례(환경 정규화
상한, 비차단 print)와 같은 방식으로 아주 관대한 상한만 건다(CI 셰어드
러너의 CPU 편차로 상시 적색이 되는 걸 피하면서도, N+1류 회귀는 왕복 수
단언이 잡는다)."""


async def test_repeated_mirror_calls_stay_within_budget() -> None:
    risk_repo = FakeRiskGateRepository()
    mandate_repo = FakeMandateRepository()
    connection_repo = UnusedConnectionRepository()

    started = time.perf_counter()
    for _ in range(_CALL_COUNT):
        subscription = _subscription()
        signal = _signal(source_listing=subscription.source_listing)
        intent = await mirror_signal(
            risk_repo,
            mandate_repo,
            connection_repo,
            subscription=subscription,
            signal=signal,
            now=NOW,
        )
        assert intent.paper_only is True
    elapsed_seconds = time.perf_counter() - started

    print(
        f"\nmirror_signal latency (n={_CALL_COUNT}, in-memory fakes): "
        f"{elapsed_seconds * 1000:.2f}ms total, "
        f"{(elapsed_seconds / _CALL_COUNT) * 1000:.3f}ms/call "
        f"(ceiling={_LATENCY_CEILING_SECONDS}s total)"
    )
    assert risk_repo.list_active_controls_calls == _CALL_COUNT
    assert risk_repo.insert_evaluation_calls == _CALL_COUNT
    assert elapsed_seconds < _LATENCY_CEILING_SECONDS, (
        f"{_CALL_COUNT}회 mirror_signal 호출이 {elapsed_seconds:.3f}s로 상한"
        f"({_LATENCY_CEILING_SECONDS}s)을 넘었다 — 회귀 의심."
    )
