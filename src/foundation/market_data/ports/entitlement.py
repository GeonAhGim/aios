"""LA-24 — 데이터 이용권(entitlement) 판정 포트 + PAPER 기본 구현.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-24.

포트 계약은 `allowed(subject, feed) -> Entitlement` 하나다. 판정 결과·주체·
피드 타입은 DC-9(`domain/entitlement/policy.py`, task-1179)가 이미 정의한
`Entitlement`/`EntitlementSubject`/`FeedRequest`를 그대로 쓴다 — 이 리프는
"같은 포트의 정책 구현으로 DC-9가 교체한다(이중 정의 금지)"는 스펙 문장에
따라 타입을 새로 만들지 않는다. 라우터(`src/api/routers/market_data.py`)는
이 Protocol만 알고, 어떤 구현이 꽂혔는지는 `src/api/foundation_deps.py`가
정한다.

기본 구현 `PaperTenantVenueEntitlement`는 스펙 문장 "자기 테넌트가 등록한
벤처만, PAPER는 delayed=0"을 그대로 옮긴 것이다:

* "등록한 벤처" = DC-8 마이그레이션(9049e2b6b0b7)의 `entitlements` 테이블에
  그 테넌트가 가진 미만료 행의 `venue` 집합. 이 집합을 읽는 I/O는
  `VenueRegistrySource`(adapters/postgres_tenant_venues.py)에 위임하고,
  이 모듈은 SQL을 갖지 않는다.
* 허용이면 항상 `mode="delayed", delayed_seconds=0`(PAPER 스콥에는 실시간
  피드 구분이 없다). timeframe·feed_type·subject 단위 세분 판정은 하지
  않는다 — 그것이 DC-9가 교체할 정책의 몫이다.
* 등록 벤처가 없으면 `NO_GRANT`로 거부한다(fail-closed — 정보 결손은 허용이
  아니라 거부). 다른 테넌트의 등록은 아예 조회 대상이 아니므로
  `TENANT_MISMATCH`는 이 구현에서 발생하지 않는다.

**미검증**: "등록한 벤처"를 `entitlements` 행으로 해석한 것은 이 리프의
결정이다(스펙 §9 행은 저장 출처를 명시하지 않는다). DC-9 정책 구현이
같은 테이블을 더 세밀하게 읽으므로 출처는 일치한다.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.entitlement.policy import (
    Entitlement,
    EntitlementDenialReason,
    EntitlementSubject,
    FeedRequest,
)
from src.foundation.market_data.ports.provider import DataProviderErrorCode

__all__ = [
    "EntitlementPort",
    "PaperTenantVenueEntitlement",
    "VenueRegistrySource",
]


@runtime_checkable
class EntitlementPort(Protocol):
    async def allowed(self, subject: EntitlementSubject, feed: FeedRequest) -> Entitlement:
        """`subject`가 `feed`를 조회할 수 있는지 판정한다. 거부는 예외가 아니라
        `Entitlement(allowed=False, ...)`로 돌려준다 — HTTP 상태코드 번역
        (타 테넌트 404 동형)은 라우터 소관."""
        ...


@runtime_checkable
class VenueRegistrySource(Protocol):
    async def registered_venues(self, tenant_id: UUID) -> frozenset[Venue]:
        """`tenant_id`가 등록한(미만료 이용권을 가진) 벤처 집합. 없으면 빈 집합."""
        ...


class PaperTenantVenueEntitlement:
    """기본 구현 — 모듈 docstring의 규칙 그대로. 순수 판정 + 주입된 I/O 한 번."""

    def __init__(self, source: VenueRegistrySource) -> None:
        self._source = source

    async def allowed(self, subject: EntitlementSubject, feed: FeedRequest) -> Entitlement:
        venues = await self._source.registered_venues(subject.tenant_id)
        if feed.venue not in venues:
            return Entitlement(
                allowed=False,
                mode=None,
                delayed_seconds=None,
                error_code=DataProviderErrorCode.DATA_ENTITLEMENT_DENIED,
                reason=EntitlementDenialReason.NO_GRANT,
            )
        return Entitlement(
            allowed=True, mode="delayed", delayed_seconds=0, error_code=None, reason=None
        )
