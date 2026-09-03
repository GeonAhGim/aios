"""DC-9 — 테넌트/사용자 데이터 이용권(entitlement) 판정(순수).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-9, §3.1(SPI 에러 taxonomy 중 `DATA_ENTITLEMENT_DENIED`), §9.2 DC-9.

이 모듈은 이용권 저장 스키마(`entitlements` 테이블, DC-8 소관)를 모른다 —
호출자(application/adapters)가 이미 읽어 넘긴 `EntitlementGrant` 순수 DTO
목록을 받아 `allowed(subject, feed) -> Entitlement`로 판정만 한다. HTTP
403 응답 생성이나 라우터 배선은 하지 않는다(§3.3 EXCEPTION_MAP 소관,
task-1179 decision) — 거부 시 `Entitlement.error_code`에 DC-5
(`ports/provider.py`)가 이미 정의한 `DataProviderErrorCode.
DATA_ENTITLEMENT_DENIED`를 그대로 담아, 그 값이 §3.3 taxonomy의 403으로
매핑 가능한 구조화 값임을 보장한다(중복 정의 대신 단일출처 재사용).

테넌트 식별자는 PLT-28 `resolve_tenant_context`(task-1090)가 확립한 개념을
그대로 쓴다 — `tenant_id`/`subject_id`는 `TenantContext`와 동일하게 `UUID`이고,
P0 스콥에서는 `tenant_id == subject_id`(개인 계정)다. 교차 테넌트 열람 차단은
LA-22(task-825)와 동일 원칙으로 "테넌트 불일치=거부"가 기본값이다 — `subject`가
들고 온 `grants` 중 `tenant_id`가 다른 항목은 판정에서 아예 제외한다(설령
uuid나 캐시 오염으로 섞여 들어왔더라도 신뢰하지 않는다, fail-closed).

판정은 4단계 깔때기다(각 단계에서 후보가 전부 걸러지면 그 단계의 사유가
거부 사유가 된다): ① 테넌트/사용자 스코프 → ② 만료 → ③ venue·자산군·종목·TF
스코프 → ④ 실시간 권한. ①②③ 어느 단계든 후보가 0개로 줄면 거부
(fail-closed — 이용권 정보가 결손이면 허용이 아니라 거부). ④에서
실시간을 요청했는데 스코프에 맞는 이용권이 지연 피드만 허용하면
거부가 아니라 부분허용(`mode="delayed"`)이다.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, model_validator

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.ports.provider import DataProviderErrorCode

__all__ = [
    "EntitlementGrant",
    "EntitlementSubject",
    "FeedRequest",
    "EntitlementDenialReason",
    "Entitlement",
    "allowed",
]


class EntitlementGrant(BaseModel, frozen=True):
    """이용권 레코드 1건의 순수 표현(`entitlements` 테이블 1행에 대응 —
    이 모듈은 그 테이블 스키마를 모른다, DC-8 소관)."""

    tenant_id: UUID
    subject_id: UUID | None
    """`None`이면 테넌트 전체(모든 사용자)에 적용되는 이용권."""
    venue: Venue
    asset_class: AssetClass
    instrument_ids: frozenset[str] | None
    """`None`이면 `venue`×`asset_class` 전체 종목에 적용."""
    timeframes: frozenset[Timeframe]
    realtime: bool
    delayed_seconds: int
    expires_at: AwareDatetime | None


class EntitlementSubject(BaseModel, frozen=True):
    """판정 대상(누가 묻는가) + 그가 보유한 이용권 목록. `resolve_tenant_context`
    가 발급한 `TenantContext`와 동일한 `tenant_id`/`subject_id` 개념이다."""

    tenant_id: UUID
    subject_id: UUID
    grants: tuple[EntitlementGrant, ...]


class FeedRequest(BaseModel, frozen=True):
    """무엇을 요청하는가(누가 묻는지는 `EntitlementSubject`가 이미 담는다)."""

    venue: Venue
    asset_class: AssetClass
    instrument_id: str
    timeframe: Timeframe
    want_realtime: bool


class EntitlementDenialReason(str, Enum):
    NO_GRANT = "NO_GRANT"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    EXPIRED = "EXPIRED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class Entitlement(BaseModel, frozen=True):
    """판정 결과. 허용/거부가 서로 배타적인 필드 조합만 표현하도록
    `model_validator`로 강제한다(잘못된 절반-허용 상태 생성 자체를 막는다)."""

    allowed: bool
    mode: Literal["realtime", "delayed"] | None
    delayed_seconds: int | None
    error_code: DataProviderErrorCode | None
    reason: EntitlementDenialReason | None

    @model_validator(mode="after")
    def _allow_deny_are_exclusive(self) -> Entitlement:
        if self.allowed:
            if self.mode is None or self.error_code is not None or self.reason is not None:
                raise ValueError("allowed=True는 mode만 채우고 error_code/reason은 비워야 한다")
            if self.mode == "delayed" and self.delayed_seconds is None:
                raise ValueError("mode='delayed'는 delayed_seconds가 필요하다")
        else:
            if (
                self.mode is not None
                or self.delayed_seconds is not None
                or self.error_code is None
                or self.reason is None
            ):
                raise ValueError("allowed=False는 error_code/reason만 채우고 mode는 비워야 한다")
        return self


def _deny(reason: EntitlementDenialReason) -> Entitlement:
    return Entitlement(
        allowed=False,
        mode=None,
        delayed_seconds=None,
        error_code=DataProviderErrorCode.DATA_ENTITLEMENT_DENIED,
        reason=reason,
    )


def _matches_scope(grant: EntitlementGrant, feed: FeedRequest) -> bool:
    if grant.venue != feed.venue or grant.asset_class != feed.asset_class:
        return False
    if grant.instrument_ids is not None and feed.instrument_id not in grant.instrument_ids:
        return False
    return feed.timeframe in grant.timeframes


def allowed(subject: EntitlementSubject, feed: FeedRequest, as_of: datetime) -> Entitlement:
    """`subject`가 `feed`를 `as_of` 시점에 조회할 수 있는지 판정한다.

    `as_of`는 만료 판정에 쓰는 결정론적 시계 입력이다(순수 함수는 현재
    시각을 스스로 읽지 않는다) — 호출자가 tz-aware UTC로 넘긴다.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of는 tz-aware datetime만 받는다")

    tenant_owned = [g for g in subject.grants if g.tenant_id == subject.tenant_id]
    if not tenant_owned:
        if subject.grants:
            return _deny(EntitlementDenialReason.TENANT_MISMATCH)
        return _deny(EntitlementDenialReason.NO_GRANT)

    owned = [g for g in tenant_owned if g.subject_id is None or g.subject_id == subject.subject_id]
    if not owned:
        return _deny(EntitlementDenialReason.NO_GRANT)

    active = [g for g in owned if g.expires_at is None or g.expires_at > as_of]
    if not active:
        return _deny(EntitlementDenialReason.EXPIRED)

    scoped = [g for g in active if _matches_scope(g, feed)]
    if not scoped:
        return _deny(EntitlementDenialReason.OUT_OF_SCOPE)

    if feed.want_realtime and any(g.realtime for g in scoped):
        return Entitlement(
            allowed=True, mode="realtime", delayed_seconds=None, error_code=None, reason=None
        )

    delay = min(g.delayed_seconds for g in scoped)
    return Entitlement(
        allowed=True, mode="delayed", delayed_seconds=delay, error_code=None, reason=None
    )
