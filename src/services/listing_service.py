"""13.2 — 전략 리스팅 API (생성 + 검증 제출).

Spec: 기능설계문서_v1.20.md#FD-13.1/FD-13.1b, 13번 §13.5, 15번 §15.5

리스팅 생성(DRAFT)과 검증 제출(PENDING_VERIFICATION)은 별도 액션으로
분리한다(재점검 라운드 정정 — 생성 즉시 자동으로 검증 대기열에 넣지
않고, 판매자가 가격 등을 다시 검토할 여지를 준 뒤 명시적으로 제출).

3개월 Paper Trading 이력 확인(9.5-A 원칙)은 FD-16(전략 실행)이 아직
없어 그 이력을 실제로 추적할 방법이 없다 — verify_paper_trading_eligibility
DI 콜백으로 주입받는다(이 세션에서 반복 적용한 패턴, WatchdogService.
compute_equity/SurgeDetector.verify_provenance 등과 동일).

create_listing()은 users.seller_suspended도 확인한다 — FD-18.4(판매자
정지)가 이 플래그를 토글하면 신규 리스팅 생성이 즉시 거부된다.

편차(ADR-2026-08-29 §2): seller_type='PLATFORM'(플랫폼 직접판매) 리스팅은
create_platform_listing()이라는 별도 경로로 만든다 — 제3자 판매자용
DRAFT→PENDING_VERIFICATION→LISTED 검증 파이프라인을 그대로 재사용하지
않고 관리자 등록 즉시 LISTED로 게시한다(아래 메서드 docstring 참조).
커미션 계산(commission.py)은 그대로 재사용한다 — 동일 커미션 구조로
취급하기로 결정했기 때문.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.services.wallet_service import PLATFORM_HOUSE_USER_ID

VerifyEligibilityFn = Callable[[str, str], Awaitable[bool]]


class ListingError(Exception):
    """FD-13.1/13.1b 실패 — 라우터가 400/403/404로 변환."""


class Listing(BaseModel):
    id: int
    strategy_id: str
    strategy_version: str
    seller_user_id: UUID
    price: Decimal | None
    status: str
    created_at: datetime
    seller_type: str = "USER"


def _validate_price(price: Decimal | None) -> None:
    """전수감사(docs/FULL_AUDIT_2026-09-02.md §2) 반영 — 음수 가격은 구매
    시점에 지갑 차감이 아니라 증액이 되므로 서비스 계층에서도 거부한다
    (API 스키마 `Field(ge=0)`·DB CHECK와 함께 세 겹)."""
    if price is not None and price < 0:
        raise ListingError("가격은 0 이상이어야 합니다.")


class ListingService:
    def __init__(
        self, pool: asyncpg.Pool, *, verify_paper_trading_eligibility: VerifyEligibilityFn
    ) -> None:
        self._pool = pool
        self._verify_eligibility = verify_paper_trading_eligibility

    async def create_listing(
        self,
        seller_user_id: UUID,
        strategy_id: str,
        strategy_version: str,
        price: Decimal | None,
    ) -> Listing:
        _validate_price(price)
        async with self._pool.acquire() as conn:
            owner_user_id = await conn.fetchval(
                "SELECT owner_user_id FROM strategies WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                strategy_version,
            )
            if owner_user_id is None:
                raise ListingError("존재하지 않는 전략입니다.")
            if owner_user_id != seller_user_id:
                raise ListingError("본인이 소유한 전략만 리스팅할 수 있습니다.")

            seller_suspended = await conn.fetchval(
                "SELECT seller_suspended FROM users WHERE user_id = $1", seller_user_id
            )
            if seller_suspended:
                raise ListingError("판매 정지된 계정은 신규 리스팅을 등록할 수 없습니다.")

            row = await conn.fetchrow(
                "INSERT INTO strategy_listings "
                "(strategy_id, strategy_version, seller_user_id, price) "
                "VALUES ($1, $2, $3, $4) RETURNING *",
                strategy_id,
                strategy_version,
                seller_user_id,
                price,
            )
        return Listing(**dict(row))

    async def create_platform_listing(
        self, strategy_id: str, strategy_version: str, price: Decimal | None
    ) -> Listing:
        """ADR-2026-08-29 §2 — 플랫폼이 직접 등록하는 리스팅(seller_type=
        'PLATFORM')은 제3자 판매자용 사기방지 검증 파이프라인(DRAFT→
        PENDING_VERIFICATION→LISTED, submit_for_verification/decide)을
        거칠 필요가 없다 — 관리자가 등록하는 행위 자체가 이미 검증이므로
        LISTED로 즉시 게시한다. 판매자는 wallet_service.PLATFORM_HOUSE_
        USER_ID(하우스 계정) 고정 — 이 계정은 정지 대상이 아니라 seller_
        suspended 확인도 건너뛴다."""
        _validate_price(price)
        async with self._pool.acquire() as conn:
            owner_user_id = await conn.fetchval(
                "SELECT owner_user_id FROM strategies WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                strategy_version,
            )
            if owner_user_id is None:
                raise ListingError("존재하지 않는 전략입니다.")

            row = await conn.fetchrow(
                "INSERT INTO strategy_listings "
                "(strategy_id, strategy_version, seller_user_id, price, seller_type, status) "
                "VALUES ($1, $2, $3, $4, 'PLATFORM', 'LISTED') RETURNING *",
                strategy_id,
                strategy_version,
                PLATFORM_HOUSE_USER_ID,
                price,
            )
        return Listing(**dict(row))

    async def submit_for_verification(self, listing_id: int, seller_user_id: UUID) -> Listing:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM strategy_listings WHERE id = $1", listing_id
            )
            if row is None:
                raise ListingError("존재하지 않는 리스팅입니다.")
            if row["seller_user_id"] != seller_user_id:
                raise ListingError("본인의 리스팅만 제출할 수 있습니다.")
            if row["status"] != "DRAFT":
                raise ListingError(f"DRAFT 상태에서만 제출할 수 있습니다(현재: {row['status']}).")

            eligible = await self._verify_eligibility(
                row["strategy_id"], row["strategy_version"]
            )
            if not eligible:
                raise ListingError("3개월 이상의 Paper Trading 이력이 필요합니다.")

            updated = await conn.fetchrow(
                "UPDATE strategy_listings SET status = 'PENDING_VERIFICATION' "
                "WHERE id = $1 RETURNING *",
                listing_id,
            )
        return Listing(**dict(updated))
