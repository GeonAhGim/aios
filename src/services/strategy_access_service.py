"""13.5 — 구매한 전략 실행 연동 (실행 접근권한 판정).

Spec: 기능설계문서_v1.20.md#FD-13.4, 정책문서 10.3-B/4.10 교차테넌트 리스크2

owner_user_id(원 제작자)는 그대로 유지하고, 실행 접근권한만 별도로
판정한다 — 소유자 본인이거나, **결제가 확정(payment_status='CONFIRMED')
된** 구매 기록이 있는 구매자만 전략의 FSM 정의(실행에 필요한 상세
로직)에 접근할 수 있다. FD-13.4 원문 자체가 "구매 완료 직후"가 아니라
"결제 확인(FD-18.5b) 직후"로 명시 정정했다 — 입금 확인 전에 실행 권한이
생기는 구멍을 막기 위해서다. (갱신 — 앱 조립 단계에서 FD-18.5b가
/admin/payments/{purchase_id}/confirm으로 실제 노출돼 CONFIRMED 전이가
살아있는 경로가 됐다.)

10.3-B 블랙박스 원칙: 이 서비스는 strategies/strategy_purchases/
strategy_listings만 조회하고 구매자별 실행 상태(FD-16 소관)는 전혀
다루지 않는다 — 판매자가 구매자의 실행 데이터를 볼 경로 자체가 없다.

예외(FD-13.4): 판매자가 사후에 리스팅을 DELISTED해도 이미 CONFIRMED된
구매의 접근권한은 유지된다 — 이 판정 로직이 listing.status를 전혀 보지
않기 때문에 자연히 보장된다.
"""
from __future__ import annotations

import json
from uuid import UUID

import asyncpg
from pydantic import BaseModel


class StrategyAccessError(Exception):
    """FD-13.4 접근 거부 — 라우터가 403으로 변환."""


class StrategyDefinition(BaseModel):
    strategy_id: str
    version: str
    owner_user_id: UUID
    fsm_definition: dict[str, object]


class StrategyAccessService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def can_access(self, user_id: UUID, strategy_id: str, strategy_version: str) -> bool:
        async with self._pool.acquire() as conn:
            owner_user_id = await conn.fetchval(
                "SELECT owner_user_id FROM strategies WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                strategy_version,
            )
            if owner_user_id is None:
                return False
            if owner_user_id == user_id:
                return True

            confirmed_purchase = await conn.fetchval(
                """
                SELECT 1 FROM strategy_purchases p
                JOIN strategy_listings l ON l.id = p.listing_id
                WHERE l.strategy_id = $1 AND l.strategy_version = $2
                    AND p.buyer_user_id = $3 AND p.payment_status = 'CONFIRMED'
                LIMIT 1
                """,
                strategy_id,
                strategy_version,
                user_id,
            )
        return confirmed_purchase is not None

    async def get_strategy_for_execution(
        self, user_id: UUID, strategy_id: str, strategy_version: str
    ) -> StrategyDefinition:
        if not await self.can_access(user_id, strategy_id, strategy_version):
            raise StrategyAccessError("이 전략에 접근할 권한이 없습니다.")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT strategy_id, version, owner_user_id, fsm_definition FROM strategies "
                "WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                strategy_version,
            )
        return StrategyDefinition(
            strategy_id=row["strategy_id"],
            version=row["version"],
            owner_user_id=row["owner_user_id"],
            fsm_definition=json.loads(row["fsm_definition"]),
        )
