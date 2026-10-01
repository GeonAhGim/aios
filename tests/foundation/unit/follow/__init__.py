"""DEEPEN task-10119 — 원 리프 task-6704 대상, `follow` 도메인/애플리케이션
계층의 공백 보강.

`test_mirror_signal.py`가 이미 두껍게 덮는 것(listing 불일치·최대 비중
초과·최소 단위 미달·risk 게이트 DENY·실제 restricted_list 규칙 DENY·
risk_gate 저장소 쓰기 실패 전파·성능 예산)은 다시 만들지 않는다. 여기서
채우는 공백:

  - negative 3건: (1) `convert_signal`의 else 분기(도메인이 모르는
    sizing_policy — 방어적 fail-closed, 실제로는 열거형 확장 시에만
    도달 가능하지만 "새 정책값을 깜빡하고 분기 추가를 안 하면 무엇을
    하는가"를 고정하는 회귀 방지 테스트), (2) `ComplianceMandateMissingError`
    -> `MirrorGateDeniedError(gate="compliance", CM_MANDATE_MISSING)` 매핑,
    (3) `ComplianceBundleInactiveError` -> `MirrorGateDeniedError(gate=
    "compliance", CM_BUNDLE_INACTIVE)` 매핑 — 둘 다 `mirror_signal`이 두
    예외 타입을 각각 올바른 reason code로 변환하는지, 매핑이 뒤바뀌지
    않는지 확인한다.
  - 실패주입 1건: compliance 단계의 `mandate_repo.get_mandate`가 예상 못한
    예외(디비 커넥션 끊김 등)를 던지면 삼키지 않고 그대로 전파한다
    (fail-closed — DENY로 위장하지도, ALLOW로 새지도 않는다).
  - 성능 단언 1건: `convert_signal` 자체(순수 함수, I/O 없음)의 반복 호출이
    선형 시간 내에 끝난다 — 소스 웨이트 계산에 숨은 이차 복잡도가 없음을
    고정한다.

전부 인메모리 fake로 실행한다(follow leaf는 DB를 쓰지 않는다 —
`test_mirror_signal.py` 모듈 docstring과 동일한 근거).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.foundation.follow.application.mirror_signal import MirrorGateDeniedError, mirror_signal
from src.foundation.follow.contracts.v1 import FollowSubscription, SizingPolicy, SourceSignal
from src.foundation.follow.domain.mirror_rules import convert_signal
from src.foundation.mandates.domain.models import (
    Autonomy,
    MandateRevision,
    MandateRevisionState,
    PortfolioMandate,
)
from tests.foundation.unit.follow._mirror_signal_fakes import (
    NOW,
    FakeRiskGateRepository,
    UnusedConnectionRepository,
)

SOURCE_LISTING = uuid4()
SYMBOL = "ETH-USD"


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


# ---- negative: 도메인이 모르는 sizing_policy ---------------------------------


def test_unknown_sizing_policy_rejected_fail_closed() -> None:
    """`SizingPolicy`에 새 값이 추가되고 `convert_signal`의 분기가 갱신되지
    않으면 조용히 잘못된 금액을 내는 대신 명시적으로 거부해야 한다 — 데이터
    클래스는 런타임에 필드 타입을 강제하지 않으므로, 문자열로 미지의 값을
    주입해 else 분기를 직접 겨냥한다."""
    subscription = _subscription(sizing_policy="UNKNOWN_POLICY")
    signal = _signal()

    with pytest.raises(ValueError, match="FOLLOW_SIZING_POLICY_UNSUPPORTED"):
        convert_signal(subscription, signal)


# ---- 컴플라이언스 게이트 예외 매핑 (negative) ---------------------------------


@dataclass
class _MandateMissingRepository:
    """`get_mandate`가 mandate 자체를 못 찾는 상황(팔로워가 아직 mandate를
    발급받지 않음)을 고정한다 — `ComplianceMandateMissingError`가 그대로
    `MirrorGateDeniedError(gate="compliance")`로 매핑돼야 한다."""

    get_mandate_calls: int = 0

    async def get_mandate(self, tenant_id: UUID, portfolio_id: UUID | None = None) -> None:
        self.get_mandate_calls += 1
        return None


async def test_compliance_mandate_missing_maps_to_gate_denied() -> None:
    subscription = _subscription()
    signal = _signal()
    risk_repo = FakeRiskGateRepository()
    mandate_repo = _MandateMissingRepository()
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
    assert exc_info.value.reason_codes == ("CM_MANDATE_MISSING",)
    assert mandate_repo.get_mandate_calls == 1


@dataclass
class _InactiveBundleRepository:
    """mandate는 있지만 활성 revision이 `ACTIVE`가 아닌 상황(예: 방금
    PAUSED됨) — `ComplianceBundleInactiveError`도 동일하게 매핑돼야 한다."""

    revision_id: UUID
    tenant_id: UUID | None = None
    get_revision_calls: int = 0

    async def get_mandate(self, tenant_id: UUID, portfolio_id: UUID | None = None) -> Any:
        self.tenant_id = tenant_id
        return PortfolioMandate(
            id=uuid4(),
            tenant_id=tenant_id,
            subject_id=uuid4(),
            portfolio_id=portfolio_id if portfolio_id is not None else uuid4(),
            active_revision_id=self.revision_id,
            created_at=NOW,
        )

    async def get_revision(self, revision_id: UUID) -> MandateRevision | None:
        self.get_revision_calls += 1
        return MandateRevision(
            id=revision_id,
            mandate_id=uuid4(),
            revision_no=1,
            state=MandateRevisionState.PAUSED,
            max_total_exposure_pct=100.0,
            max_single_instrument_pct=100.0,
            min_cash_buffer_pct=0.0,
            max_daily_loss_pct=100.0,
            allowed_autonomy=Autonomy.PAPER,
        )


async def test_compliance_bundle_inactive_maps_to_gate_denied() -> None:
    subscription = _subscription()
    signal = _signal()
    risk_repo = FakeRiskGateRepository()
    mandate_repo = _InactiveBundleRepository(revision_id=uuid4())
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
    assert exc_info.value.reason_codes == ("CM_BUNDLE_INACTIVE",)
    assert mandate_repo.get_revision_calls == 1


# ---- 실패 주입: 컴플라이언스 조회 자체가 예상 못한 예외로 죽는 경우 -----------


class _MandateRepoConnectionLost(RuntimeError):
    pass


@dataclass
class _CrashingMandateRepository:
    async def get_mandate(self, tenant_id: UUID, portfolio_id: UUID | None = None) -> Any:
        raise _MandateRepoConnectionLost("simulated mandate repository connection loss")


async def test_mandate_repo_crash_propagates_fail_closed() -> None:
    """실패주입(D2) — compliance 조회 자체가 (DENY가 아니라) 예상 못한
    예외로 죽으면 그 예외가 그대로 전파돼야 한다. 여기서 삼켜 ALLOW로
    넘어가면 UX-A3(팔로워 자신의 게이트를 반드시 통과)가 무너진다."""
    subscription = _subscription()
    signal = _signal()
    risk_repo = FakeRiskGateRepository()
    mandate_repo = _CrashingMandateRepository()
    connection_repo = UnusedConnectionRepository()

    with pytest.raises(
        _MandateRepoConnectionLost, match="simulated mandate repository connection loss"
    ):
        await mirror_signal(
            risk_repo,
            mandate_repo,
            connection_repo,
            subscription=subscription,
            signal=signal,
            now=NOW,
        )


# ---- 성능 단언: convert_signal 반복 호출 (순수 함수, I/O 없음) ----------------

_CALL_COUNT = 2000
_LATENCY_CEILING_SECONDS = 1.0
"""`convert_signal`은 Decimal 산술과 enum 비교뿐인 순수 함수라 예산이
`mirror_signal` 전체 왕복(I/O 포함, `test_mirror_signal.py` 쪽 예산)보다
훨씬 빡빡해야 정상이다 — CI 셰어드 러너 편차를 감안해 2000회에 1초로 아주
관대하게만 잡는다(task-1521 선례처럼 비차단 print + 절대 상한)."""


@pytest.mark.perf
def test_convert_signal_repeated_calls_stay_within_budget() -> None:
    subscription = _subscription(max_notional=Decimal("1000.00"))
    signal = _signal(source_weight_pct=Decimal("50"))

    started = time.perf_counter()
    for _ in range(_CALL_COUNT):
        draft = convert_signal(subscription, signal)
        assert draft.notional == Decimal("500.00")
    elapsed_seconds = time.perf_counter() - started

    print(
        f"\nconvert_signal latency (n={_CALL_COUNT}, pure function): "
        f"{elapsed_seconds * 1000:.2f}ms total, "
        f"{(elapsed_seconds / _CALL_COUNT) * 1000:.4f}ms/call "
        f"(ceiling={_LATENCY_CEILING_SECONDS}s total)"
    )
    assert elapsed_seconds < _LATENCY_CEILING_SECONDS, (
        f"{_CALL_COUNT}회 convert_signal 호출이 {elapsed_seconds:.3f}s로 상한"
        f"({_LATENCY_CEILING_SECONDS}s)을 넘었다 — 회귀 의심."
    )
