"""DC-9 — domain/entitlement/policy 단위 테스트(테넌트/사용자 이용권 판정).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-9, §9.2 DC-9 DoD(이용권 없음·만료·범위 밖·지연-only 넷의 거부/부분허용
분기, 결손 시 fail-closed, 403 taxonomy로 매핑 가능한 구조화 사유).
docs/design/INVARIANTS.md I-10(안전/정책 컴포넌트는 배선·우회불가·증명) —
이 테스트가 그 증명이다(순수 판정 결과가 결정론적으로 거부/허용됨을 확인).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.domain.entitlement.policy import (
    Entitlement,
    EntitlementDenialReason,
    EntitlementGrant,
    EntitlementSubject,
    FeedRequest,
    allowed,
)
from src.foundation.market_data.ports.provider import DataProviderErrorCode

_TENANT = UUID("11111111-1111-1111-1111-111111111111")
_OTHER_TENANT = UUID("22222222-2222-2222-2222-222222222222")
_SUBJECT = UUID("33333333-3333-3333-3333-333333333333")
_OTHER_SUBJECT = UUID("44444444-4444-4444-4444-444444444444")


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


def _subject(*, grants: tuple[EntitlementGrant, ...]) -> EntitlementSubject:
    return EntitlementSubject(tenant_id=_TENANT, subject_id=_SUBJECT, grants=grants)


def _assert_denied(result: Entitlement, reason: EntitlementDenialReason) -> None:
    assert result.allowed is False
    assert result.mode is None
    assert result.delayed_seconds is None
    assert result.error_code == DataProviderErrorCode.DATA_ENTITLEMENT_DENIED
    assert result.reason == reason


# ---- 4대 DoD 시나리오: 거부 3종 + 부분허용 1종 ----


def test_no_grant_at_all_is_denied() -> None:
    result = allowed(_subject(grants=()), _feed(), _dt(1))
    _assert_denied(result, EntitlementDenialReason.NO_GRANT)


def test_expired_grant_is_denied() -> None:
    grant = _grant(expires_at=_dt(1))
    result = allowed(_subject(grants=(grant,)), _feed(), as_of=_dt(2))
    _assert_denied(result, EntitlementDenialReason.EXPIRED)


def test_non_expired_grant_at_exact_boundary_is_still_expired() -> None:
    """`expires_at`은 배타적 상한(half-open) — `as_of == expires_at`은 만료."""
    grant = _grant(expires_at=_dt(1))
    result = allowed(_subject(grants=(grant,)), _feed(), as_of=_dt(1))
    _assert_denied(result, EntitlementDenialReason.EXPIRED)


def test_different_venue_is_out_of_scope() -> None:
    grant = _grant(venue=Venue.BITGET)
    feed = _feed(venue=Venue.KIS_KRX, asset_class=AssetClass.KR_EQUITY)
    result = allowed(_subject(grants=(grant,)), feed, _dt(1))
    _assert_denied(result, EntitlementDenialReason.OUT_OF_SCOPE)


def test_different_instrument_not_in_grant_list_is_out_of_scope() -> None:
    grant = _grant(instrument_ids=frozenset({"ETH-USDT"}))
    result = allowed(_subject(grants=(grant,)), _feed(instrument_id="BTC-USDT"), _dt(1))
    _assert_denied(result, EntitlementDenialReason.OUT_OF_SCOPE)


def test_different_timeframe_not_covered_is_out_of_scope() -> None:
    grant = _grant(timeframes=frozenset({Timeframe.H1}))
    result = allowed(_subject(grants=(grant,)), _feed(timeframe=Timeframe.D1), _dt(1))
    _assert_denied(result, EntitlementDenialReason.OUT_OF_SCOPE)


def test_realtime_wanted_but_grant_delayed_only_is_partial_allow_not_deny() -> None:
    """네 번째 DoD 분기: 실시간 권한 없이 지연만 허용 -> 거부가 아니라
    부분허용(mode='delayed')."""
    grant = _grant(realtime=False, delayed_seconds=300)
    result = allowed(_subject(grants=(grant,)), _feed(want_realtime=True), _dt(1))
    assert result.allowed is True
    assert result.mode == "delayed"
    assert result.delayed_seconds == 300
    assert result.error_code is None
    assert result.reason is None


def test_realtime_grant_and_realtime_requested_is_full_allow() -> None:
    grant = _grant(realtime=True)
    result = allowed(_subject(grants=(grant,)), _feed(want_realtime=True), _dt(1))
    assert result.allowed is True
    assert result.mode == "realtime"
    assert result.delayed_seconds is None


def test_delayed_requested_with_realtime_grant_is_delayed_mode() -> None:
    """실시간 권한이 있어도 호출자가 지연을 요청하면 지연으로 응답한다
    (요청 의도를 넘어서는 강제 실시간 허용 없음)."""
    grant = _grant(realtime=True, delayed_seconds=60)
    result = allowed(_subject(grants=(grant,)), _feed(want_realtime=False), _dt(1))
    assert result.allowed is True
    assert result.mode == "delayed"
    assert result.delayed_seconds == 60


# ---- 테넌트 불일치(LA-22 원칙): fail-closed, 다른 사유와 구분 ----


def test_grant_belonging_to_other_tenant_is_denied_as_tenant_mismatch() -> None:
    grant = _grant(tenant_id=_OTHER_TENANT)
    result = allowed(_subject(grants=(grant,)), _feed(), _dt(1))
    _assert_denied(result, EntitlementDenialReason.TENANT_MISMATCH)


def test_grant_scoped_to_other_subject_in_same_tenant_is_no_grant() -> None:
    """같은 테넌트지만 다른 사용자 전용 이용권은 이 subject에게는 '없음'과
    동치다(테넌트 불일치와는 다른 사유)."""
    grant = _grant(subject_id=_OTHER_SUBJECT)
    result = allowed(_subject(grants=(grant,)), _feed(), _dt(1))
    _assert_denied(result, EntitlementDenialReason.NO_GRANT)


def test_tenant_wide_grant_with_subject_id_none_applies_to_any_subject() -> None:
    grant = _grant(subject_id=None)
    result = allowed(_subject(grants=(grant,)), _feed(), _dt(1))
    assert result.allowed is True


# ---- 결손 데이터는 허용이 아니라 거부(fail-closed) ----


def test_missing_grants_field_defaults_to_deny_not_allow() -> None:
    """`grants=()`(정보 결손)가 곧 fail-closed 거부다 — 빈 값이 '전부 허용'으로
    해석되는 경로가 없음을 명시적으로 단언."""
    result = allowed(_subject(grants=()), _feed(), _dt(1))
    assert result.allowed is False


def test_as_of_naive_datetime_is_rejected() -> None:
    grant = _grant()
    with pytest.raises(ValueError, match="tz-aware"):
        allowed(_subject(grants=(grant,)), _feed(), datetime(2026, 1, 1))


# ---- Entitlement 결과 자체의 불변조건(허용/거부 필드 조합 배타성) ----


def test_entitlement_rejects_allowed_true_with_error_code_set() -> None:
    with pytest.raises(ValidationError):
        Entitlement(
            allowed=True,
            mode="realtime",
            delayed_seconds=None,
            error_code=DataProviderErrorCode.DATA_ENTITLEMENT_DENIED,
            reason=None,
        )


def test_entitlement_rejects_denied_without_reason() -> None:
    with pytest.raises(ValidationError):
        Entitlement(
            allowed=False,
            mode=None,
            delayed_seconds=None,
            error_code=DataProviderErrorCode.DATA_ENTITLEMENT_DENIED,
            reason=None,
        )


def test_entitlement_rejects_delayed_mode_without_delayed_seconds() -> None:
    with pytest.raises(ValidationError):
        Entitlement(
            allowed=True, mode="delayed", delayed_seconds=None, error_code=None, reason=None
        )


# ---- 다건 이용권: 스코프 맞는 건만 골라 판정(가장 관대한 지연 채택) ----


def test_multiple_grants_scoped_one_wins_over_out_of_scope_ones() -> None:
    wrong_venue = _grant(venue=Venue.KIS_KRX, asset_class=AssetClass.KR_EQUITY)
    matching = _grant(realtime=False, delayed_seconds=120)
    result = allowed(_subject(grants=(wrong_venue, matching)), _feed(want_realtime=True), _dt(1))
    assert result.allowed is True
    assert result.mode == "delayed"
    assert result.delayed_seconds == 120


def test_multiple_delayed_grants_pick_most_generous_smallest_delay() -> None:
    slow = _grant(realtime=False, delayed_seconds=900)
    fast = _grant(realtime=False, delayed_seconds=60)
    result = allowed(_subject(grants=(slow, fast)), _feed(want_realtime=True), _dt(1))
    assert result.delayed_seconds == 60


def test_uuid_type_used_for_tenant_and_subject_matches_tenant_context_concept() -> None:
    """PLT-28 TenantContext와 동일하게 tenant_id/subject_id가 UUID임을 계약으로
    고정 — 다른 타입(str 등) 재정의를 막는 회귀 가드."""
    subject = _subject(grants=())
    assert isinstance(subject.tenant_id, UUID)
    assert isinstance(subject.subject_id, UUID)
    assert subject.tenant_id != uuid4()
