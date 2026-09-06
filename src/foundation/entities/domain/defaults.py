"""FA-1 — 개인 사용자 기본 엔티티 계층 자동 생성 규칙(순수).

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-1 (§2.1·§9 FA-1 DoD).

DoD: "기존 단일계좌 UX 무변경". 지금까지 사용자는 법인·펀드·포트폴리오
개념 없이 계좌 하나로 거래해 왔다 — FA-3~6이 모든 주문·포지션·원장 쓰기에
`fund_id`/`portfolio_id`를 강제하기 시작하면, 기존 사용자마다 자동으로
"개인용 기본 법인/펀드/포트폴리오/서브계좌" 한 벌이 있어야 그 강제가
UX를 깨지 않는다.

**ID 생성을 결정론(UUIDv5, user_id 기반)으로 정의하는 이유**: 이 계층은
FA-2(영속화) 이전에도 여러 곳(라우터 검증, 캐시 키, 테스트)에서 같은
id를 재현할 수 있어야 한다. 매번 무작위 UUID를 새로 발급하면 "이 사용자의
기본 포트폴리오가 무엇인가"를 조회 없이 답할 수 없고, FA-3 소급
백필(기존 orders/fills에 fund_id/portfolio_id 채우기)도 저장된 매핑
테이블에 의존하게 된다. UUIDv5는 (고정 네임스페이스, 이산자, user_id)
튜플로부터 항상 같은 id를 산출하므로 — 조회 없이, 마이그레이션 스크립트와
런타임 코드가 각자 계산해도 — 항상 일치한다(멱등). 네임스페이스 상수는
한 번 고정되면 절대 바뀌지 않는다(바뀌면 기존 사용자 전원의 기본 계층
id가 달라져 FA-3 백필이 깨진다).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid5

from src.data.models.base import Currency
from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio, SubAccount

# 고정 네임스페이스. `uuid.uuid5(uuid.NAMESPACE_URL,
# "https://aios.internal/foundation/entities/default-hierarchy/v1")`의
# 결과를 하드코딩했다 — 이 상수를 바꾸면 기존 사용자의 기본 계층 id가
# 전부 바뀐다(절대 변경 금지, 변경이 필요하면 신규 상수 + 마이그레이션).
_DEFAULT_HIERARCHY_NAMESPACE = UUID("5c81612c-72bf-5cda-8e53-109f3d102e52")

_DEFAULT_ENTITY_NAME = "Personal Account"


def default_entity_id(user_id: UUID) -> UUID:
    return uuid5(_DEFAULT_HIERARCHY_NAMESPACE, f"legal_entity:{user_id}")


def default_fund_id(user_id: UUID) -> UUID:
    return uuid5(_DEFAULT_HIERARCHY_NAMESPACE, f"fund:{user_id}")


def default_portfolio_id(user_id: UUID) -> UUID:
    return uuid5(_DEFAULT_HIERARCHY_NAMESPACE, f"portfolio:{user_id}")


def default_sub_account_id(user_id: UUID) -> UUID:
    return uuid5(_DEFAULT_HIERARCHY_NAMESPACE, f"sub_account:{user_id}")


@dataclass(frozen=True)
class DefaultHierarchy:
    legal_entity: LegalEntity
    fund: Fund
    portfolio: Portfolio
    sub_account: SubAccount


def build_default_hierarchy(
    *,
    user_id: UUID,
    tenant_id: UUID,
    base_currency: Currency,
    jurisdiction: str,
    region_tag: str,
    venue_account_ref: str,
    inception: date,
    name: str = _DEFAULT_ENTITY_NAME,
) -> DefaultHierarchy:
    """단일 사용자용 4단 계층을 결정론적으로 구성한다. 이 함수는 순수하다 —
    영속화 여부·존재 여부를 확인하지 않는다(멱등 upsert는 FA-2 어댑터 책임).
    `jurisdiction`/`region_tag`/`base_currency`/`venue_account_ref`는
    사용자 프로필·거래소 연결 정보에서 오는 값이라 여기서 기본값을
    가정하지 않는다 — 결정론이 필요한 것은 id뿐이다."""
    entity = LegalEntity(
        entity_id=default_entity_id(user_id),
        tenant_id=tenant_id,
        name=name,
        jurisdiction=jurisdiction,
        region_tag=region_tag,
    )
    fund = Fund(
        fund_id=default_fund_id(user_id),
        entity_id=entity.entity_id,
        base_currency=base_currency,
        inception=inception,
    )
    portfolio = Portfolio(
        portfolio_id=default_portfolio_id(user_id),
        fund_id=fund.fund_id,
        venue_account_ref=venue_account_ref,
    )
    sub_account = SubAccount(
        sub_account_id=default_sub_account_id(user_id),
        portfolio_id=portfolio.portfolio_id,
        owner_ref=user_id,
    )
    return DefaultHierarchy(
        legal_entity=entity, fund=fund, portfolio=portfolio, sub_account=sub_account
    )
