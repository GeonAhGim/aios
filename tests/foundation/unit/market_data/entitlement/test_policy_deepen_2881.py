"""DC-9 `domain/entitlement/policy.py` — DEEPEN(task-2881,
docs/audit/DEPTH_DC_RD.md#1179) D1 -> D3 증빙.

기존 test_policy.py는 4단계 깔때기(테넌트/사용자 -> 만료 -> 스코프 -> 실시간)의
각 분기를 단건 grant로 순차 증명했다(D1) — 감사에서 "실패주입 없음, 성능단언
없음, 게이트적색 재현 없음, D3 요소 없음"으로 지적됐다(1179행). 이 파일이
그 부족분을 채운다. `policy.py`는 순수 함수라 DB/네트워크가 없으므로, 이
파일에서 "실패주입"이란 pydantic 계약(AwareDatetime·frozenset enum)이 조용한
통과를 막는지를, "게이트 적색 재현"이란 깔때기 4단계 전체를 하나의 grants
집합에 겹쳐 쌓아 재생하며 각 단계 경계에서 정확한 사유로 갈라지는지를,
"D3"란 순수 함수에 공유 가변 상태가 없어 다중 스레드 동시 호출도 서로
오염되지 않는지를 뜻한다. 새 기능 없음, 깊이만 올림.

1. 실패 주입 — naive expires_at, 미지의 venue/asset_class/timeframe 문자열,
   `instrument_ids=frozenset()`(공집합, `None`과 다름) 등이 조용히 통과하지
   않고 각각 `ValidationError`/OUT_OF_SCOPE로 거부됨을 증명한다.
2. 성능 단언 — 대량 grants(5,000건)를 가진 subject에 대한 판정이 절대시간
   예산 내에 있음을 증명한다(선형 스캔이 이차로 퇴화하지 않았는지).
3. 게이트 적색 재현 — 하나의 grants 집합을 단계적으로 넓혀가며 재생한다:
   빈 목록(NO_GRANT) -> 타 테넌트만 추가(TENANT_MISMATCH) -> 자기 테넌트지만
   만료(EXPIRED) -> 스코프 밖(OUT_OF_SCOPE) -> 지연만(부분허용) -> 실시간
   추가(완전허용). 각 단계 전이에서 이전 단계의 결손이 다음 단계로 새지
   않고 정확한 사유/모드로만 갈라짐을 증명한다.
4. 동시 다중 인스턴스(D3) — 스레드풀로 서로 다른 subject/feed/as_of 조합을
   동시에 `allowed()`에 넣어도 결과가 섞이지 않음을 증명한다.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.domain.entitlement.policy import (
    EntitlementDenialReason,
    EntitlementGrant,
    EntitlementSubject,
    FeedRequest,
    allowed,
)

_TENANT = UUID("11111111-1111-1111-1111-111111111111")
_OTHER_TENANT = UUID("22222222-2222-2222-2222-222222222222")
_SUBJECT = UUID("33333333-3333-3333-3333-333333333333")


def _dt(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, 0, tzinfo=timezone.utc)


def _grant(
    *,
    tenant_id: UUID = _TENANT,
    subject_id: UUID | None = _SUBJECT,
    venue: Venue = Venue.BITGET,
    asset_class: AssetClass = AssetClass.CRYPTO,
    instrument_ids: frozenset[str] | None = frozenset({"BTC-USDT"}),
    timeframes: frozenset[Timeframe] = frozenset({Timeframe.D1}),
    realtime: bool = True,
    delayed_seconds: int = 900,
    expires_at: datetime | None = None,
) -> EntitlementGrant:
    return EntitlementGrant(
        tenant_id=tenant_id,
        subject_id=subject_id,
        venue=venue,
        asset_class=asset_class,
        instrument_ids=instrument_ids,
        timeframes=timeframes,
        realtime=realtime,
        delayed_seconds=delayed_seconds,
        expires_at=expires_at,
    )


def _feed(
    *,
    venue: Venue = Venue.BITGET,
    asset_class: AssetClass = AssetClass.CRYPTO,
    instrument_id: str = "BTC-USDT",
    timeframe: Timeframe = Timeframe.D1,
    want_realtime: bool = True,
) -> FeedRequest:
    return FeedRequest(
        venue=venue,
        asset_class=asset_class,
        instrument_id=instrument_id,
        timeframe=timeframe,
        want_realtime=want_realtime,
    )


def _subject(
    *, tenant_id: UUID = _TENANT, subject_id: UUID = _SUBJECT, grants: tuple[EntitlementGrant, ...]
) -> EntitlementSubject:
    return EntitlementSubject(tenant_id=tenant_id, subject_id=subject_id, grants=grants)


# ---- 실패 주입 ----


def test_naive_expires_at_is_rejected_at_construction() -> None:
    """`expires_at`도 `AwareDatetime`이다 — naive 값이 슬쩍 들어와도 조용히
    UTC로 취급되지 않고 grant 구성 시점에 즉시 거부돼야 한다."""
    with pytest.raises(ValidationError):
        EntitlementGrant(
            tenant_id=_TENANT,
            subject_id=_SUBJECT,
            venue=Venue.BITGET,
            asset_class=AssetClass.CRYPTO,
            instrument_ids=None,
            timeframes=frozenset({Timeframe.D1}),
            realtime=True,
            delayed_seconds=0,
            expires_at=datetime(2026, 1, 1),
        )


@pytest.mark.parametrize(
    "build",
    [
        lambda: EntitlementGrant(
            tenant_id=_TENANT,
            subject_id=_SUBJECT,
            venue="NOT_A_VENUE",
            asset_class=AssetClass.CRYPTO,
            instrument_ids=None,
            timeframes=frozenset({Timeframe.D1}),
            realtime=True,
            delayed_seconds=0,
            expires_at=None,
        ),
        lambda: EntitlementGrant(
            tenant_id=_TENANT,
            subject_id=_SUBJECT,
            venue=Venue.BITGET,
            asset_class="NOT_AN_ASSET_CLASS",
            instrument_ids=None,
            timeframes=frozenset({Timeframe.D1}),
            realtime=True,
            delayed_seconds=0,
            expires_at=None,
        ),
        lambda: EntitlementGrant(
            tenant_id=_TENANT,
            subject_id=_SUBJECT,
            venue=Venue.BITGET,
            asset_class=AssetClass.CRYPTO,
            instrument_ids=None,
            timeframes=frozenset({"NOT_A_TIMEFRAME"}),
            realtime=True,
            delayed_seconds=0,
            expires_at=None,
        ),
    ],
    ids=["venue", "asset_class", "timeframes"],
)
def test_unknown_enum_value_in_grant_is_rejected_fail_closed(build) -> None:
    """오타·미지의 venue/asset_class/timeframe 문자열이 grant에 실려도 조용히
    통과해 나중에 판정 시점에 원인 모를 결과로 새는 대신, 구성 시점에 즉시
    `ValidationError`로 거부돼야 한다."""
    with pytest.raises(ValidationError):
        build()


def test_empty_instrument_ids_frozenset_means_no_instrument_is_in_scope() -> None:
    """`instrument_ids=frozenset()`(공집합)은 `None`(전체 허용)과 다르다 —
    "명시적으로 아무 종목도 없음"이므로 어떤 요청도 스코프에 들 수 없어
    fail-closed로 OUT_OF_SCOPE여야 한다(공집합이 '제한 없음'으로 오독되면
    안 됨)."""
    grant = _grant(instrument_ids=frozenset())
    result = allowed(_subject(grants=(grant,)), _feed(instrument_id="BTC-USDT"), _dt(1))
    assert result.allowed is False
    assert result.reason == EntitlementDenialReason.OUT_OF_SCOPE


def test_empty_timeframes_frozenset_means_no_timeframe_is_in_scope() -> None:
    """`timeframes=frozenset()`도 동일 원칙 — 빈 타임프레임 집합은 모든
    요청을 OUT_OF_SCOPE로 거부해야 한다."""
    grant = _grant(timeframes=frozenset())
    result = allowed(_subject(grants=(grant,)), _feed(timeframe=Timeframe.D1), _dt(1))
    assert result.allowed is False
    assert result.reason == EntitlementDenialReason.OUT_OF_SCOPE


def test_adversarial_grant_list_mixing_wrong_tenant_and_wrong_subject_is_still_fail_closed() -> (
    None
):
    """캐시 오염·조인 실수를 흉내: 같은 목록에 (a) 다른 테넌트 grant,
    (b) 같은 테넌트·다른 사용자 grant, (c) 스코프 밖 grant를 한꺼번에 섞어도
    이 subject/feed 조합엔 진짜로 유효한 grant가 하나도 없으므로 거부여야
    한다 — 섞인 노이즈 중 하나라도 오판정으로 허용을 새게 하면 안 된다."""
    noise = (
        _grant(tenant_id=_OTHER_TENANT),
        _grant(subject_id=uuid4()),
        _grant(venue=Venue.KIS_KRX, asset_class=AssetClass.KR_EQUITY),
    )
    result = allowed(_subject(grants=noise), _feed(), _dt(1))
    assert result.allowed is False
    # 타 테넌트 grant는 1단계에서 제외되고, 타 사용자 grant는 2단계에서 제외돼
    # 결국 스코프 밖 grant 하나만 후보로 남아 OUT_OF_SCOPE로 갈린다 — 노이즈
    # 중 어느 것도 허용으로 새지 않는다.
    assert result.reason == EntitlementDenialReason.OUT_OF_SCOPE


# ---- 성능 단언 ----


@pytest.mark.perf
def test_allowed_with_large_grant_list_meets_latency_budget() -> None:
    """5,000건의 grants를 가진 subject에 대한 단건 판정이 절대시간 예산
    내에 있어야 한다(선형 스캔이 이차 이상으로 퇴화하면 실시간 판정 경로가
    다건 이용권 테넌트에서 지연 SLA를 못 지킨다)."""
    n = 5_000
    budget_sec = 1.0  # 실측 로컬 <0.05s
    grants = tuple(
        _grant(
            instrument_ids=frozenset({f"SYM{i}-USDT"}),
            realtime=False,
            delayed_seconds=60 + i,
        )
        for i in range(n)
    )
    # 목표 grant는 맨 끝에 둬 최선의 조기 종료 최적화가 없어도 전수 스캔임을 보장.
    target = _grant(realtime=True)
    subject = _subject(grants=grants + (target,))

    start = time.perf_counter()
    result = allowed(subject, _feed(), _dt(1))
    elapsed = time.perf_counter() - start

    print(f"[DC-9 policy] allowed() with {n + 1} grants in {elapsed:.4f}s (budget<{budget_sec}s)")
    assert result.allowed is True
    assert result.mode == "realtime"
    assert elapsed < budget_sec, (
        f"grants {n + 1}건 판정이 예산({budget_sec}s)을 넘었습니다({elapsed:.4f}s)."
    )


@pytest.mark.perf
def test_repeated_allowed_calls_meet_throughput_budget() -> None:
    """동일 subject/feed에 대한 반복 판정(캐시 미스 시뮬레이션 — 매 호출이
    호출자 쪽에서 재구성한 새 DTO를 받는 상황)이 처리량 예산을 지켜야
    한다."""
    iterations = 10_000
    budget_sec = 6.0  # 실측 로컬 <0.3s, CI 환경 편차 감안
    grant = _grant(realtime=False, delayed_seconds=120)
    subject = _subject(grants=(grant,))
    feed = _feed()

    start = time.perf_counter()
    for i in range(iterations):
        result = allowed(subject, feed, _dt(1, hour=i % 24))
        assert result.allowed is True
    elapsed = time.perf_counter() - start

    print(
        f"[DC-9 policy] allowed() x{iterations} repeated calls in {elapsed:.3f}s "
        f"(budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"allowed() {iterations}회 반복이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


# ---- 게이트 적색 재현 — 4단계 깔때기를 한 grants 집합에 겹쳐 쌓아 재생 ----


def test_gate_red_funnel_stages_replayed_in_sequence_never_leak_wrong_reason() -> None:
    """하나의 시나리오를 단계적으로 넓혀가며 재생한다. 각 단계는 이전 단계의
    grants에 하나씩 더한 상태다 — 이전 단계에서 거부됐던 사유가 다음 단계로
    잘못 새어나오지 않고(예: TENANT_MISMATCH가 남아있는데 EXPIRED로 오판정),
    매 단계 정확히 그 단계가 의도한 사유/결과로만 갈라짐을 증명한다(§9.2
    DoD가 요구하는 4단계 각각의 독립적 정확성)."""
    feed = _feed(want_realtime=True)

    # 0단계: grants 전무 -> NO_GRANT.
    stage0 = allowed(_subject(grants=()), feed, _dt(10))
    assert stage0.reason == EntitlementDenialReason.NO_GRANT

    # 1단계: 타 테넌트 grant 추가 -> TENANT_MISMATCH(존재하지만 남의 것).
    other_tenant_grant = _grant(tenant_id=_OTHER_TENANT)
    stage1 = allowed(_subject(grants=(other_tenant_grant,)), feed, _dt(10))
    assert stage1.reason == EntitlementDenialReason.TENANT_MISMATCH

    # 2단계: 자기 테넌트 grant를 추가하되 이미 만료 -> EXPIRED
    # (타 테넌트 grant가 여전히 섞여 있어도 사유가 EXPIRED로 정확히 갈라져야 함).
    expired_own_grant = _grant(expires_at=_dt(5))
    stage2 = allowed(_subject(grants=(other_tenant_grant, expired_own_grant)), feed, _dt(10))
    assert stage2.reason == EntitlementDenialReason.EXPIRED

    # 3단계: 유효기간 내 grant를 추가하되 스코프 밖(다른 venue) -> OUT_OF_SCOPE.
    active_wrong_scope = _grant(venue=Venue.KIS_KRX, asset_class=AssetClass.KR_EQUITY)
    stage3 = allowed(
        _subject(grants=(other_tenant_grant, expired_own_grant, active_wrong_scope)),
        feed,
        _dt(10),
    )
    assert stage3.reason == EntitlementDenialReason.OUT_OF_SCOPE

    # 4단계: 스코프 맞지만 지연만 -> 거부가 아니라 부분허용(delayed)으로 전환.
    delayed_only = _grant(realtime=False, delayed_seconds=45)
    stage4 = allowed(
        _subject(grants=(other_tenant_grant, expired_own_grant, active_wrong_scope, delayed_only)),
        feed,
        _dt(10),
    )
    assert stage4.allowed is True
    assert stage4.mode == "delayed"
    assert stage4.delayed_seconds == 45
    assert stage4.error_code is None
    assert stage4.reason is None

    # 5단계: 실시간 grant까지 추가 -> 완전허용(realtime)으로 전환.
    realtime_grant = _grant(realtime=True)
    stage5 = allowed(
        _subject(
            grants=(
                other_tenant_grant,
                expired_own_grant,
                active_wrong_scope,
                delayed_only,
                realtime_grant,
            )
        ),
        feed,
        _dt(10),
    )
    assert stage5.allowed is True
    assert stage5.mode == "realtime"
    assert stage5.delayed_seconds is None


# ---- 동시 다중 인스턴스(D3) ----


def test_concurrent_allowed_calls_across_threads_do_not_cross_contaminate() -> None:
    """서로 다른 결과(거부 3종 + 부분허용 + 완전허용)를 내야 하는 5개 조합을
    스레드풀에서 반복 동시 호출한다 — 각 워커가 매번 자기 입력에 맞는
    결과만 받고, 다른 워커의 결과/예외로 오염되지 않아야 한다(순수 함수라면
    공유 가변 상태가 없어 당연해야 하나, 회귀 시 모듈 레벨 캐시 등이
    실수로 추가되는 것을 이 테스트가 잡는다)."""
    other_tenant_grant = _grant(tenant_id=_OTHER_TENANT)
    expired_grant = _grant(expires_at=_dt(1))
    out_of_scope_grant = _grant(venue=Venue.KIS_KRX, asset_class=AssetClass.KR_EQUITY)
    delayed_grant = _grant(realtime=False, delayed_seconds=77)
    realtime_grant = _grant(realtime=True)

    scenarios: dict[str, tuple[EntitlementSubject, FeedRequest, datetime, dict]] = {
        "no_grant": (
            _subject(grants=()),
            _feed(),
            _dt(1),
            {"allowed": False, "reason": EntitlementDenialReason.NO_GRANT},
        ),
        "tenant_mismatch": (
            _subject(grants=(other_tenant_grant,)),
            _feed(),
            _dt(1),
            {"allowed": False, "reason": EntitlementDenialReason.TENANT_MISMATCH},
        ),
        "expired": (
            _subject(grants=(expired_grant,)),
            _feed(),
            _dt(2),
            {"allowed": False, "reason": EntitlementDenialReason.EXPIRED},
        ),
        "out_of_scope": (
            _subject(grants=(out_of_scope_grant,)),
            _feed(),
            _dt(1),
            {"allowed": False, "reason": EntitlementDenialReason.OUT_OF_SCOPE},
        ),
        "delayed": (
            _subject(grants=(delayed_grant,)),
            _feed(want_realtime=True),
            _dt(1),
            {"allowed": True, "mode": "delayed", "delayed_seconds": 77},
        ),
        "realtime": (
            _subject(grants=(realtime_grant,)),
            _feed(want_realtime=True),
            _dt(1),
            {"allowed": True, "mode": "realtime", "delayed_seconds": None},
        ),
    }

    def _run(name: str, repeat: int) -> tuple[str, list[bool]]:
        subject, feed, as_of, expected = scenarios[name]
        outcomes = []
        for _ in range(repeat):
            result = allowed(subject, feed, as_of)
            ok = result.allowed == expected["allowed"]
            if expected["allowed"]:
                ok = ok and result.mode == expected["mode"]
                ok = ok and result.delayed_seconds == expected["delayed_seconds"]
            else:
                ok = ok and result.reason == expected["reason"]
            outcomes.append(ok)
        return name, outcomes

    names = list(scenarios) * 20  # 6종 x 20 = 120개 동시 작업, 서로 인터리빙되도록
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(_run, name, 25) for name in names]
        results = [f.result() for f in futures]

    for name, outcomes in results:
        assert all(outcomes), f"동시 실행 중 시나리오 {name!r}가 다른 워커에 오염됐다."
