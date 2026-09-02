"""13.4 — 전략 구매 API (자전거래 방지 포함).

Spec: 기능설계문서_v1.20.md#FD-13.3, 13번 §13.5

FD-15.3(위험등급 매칭 경고)이 아직 없어(FD-15 전체가 뒤 섹션) 리스크
경고 조회는 DI 콜백으로 주입받는다 — 경고가 있는데 명시적 동의가 없으면
구매를 막는다.

price_paid 기록과 함께 FD-13.7(중개수수료) 계산 결과도 같은 트랜잭션에서
기록한다 — FD-13.7 원문이 "FD-13.3 구매 처리 결과에 포함돼 함께 반환"을
명시하고 있어 이 leaf에서 함께 배선한다.

FD-17.1 이벤트 발행 — 구매 성공 시 "marketplace.purchase.requested"(구매자
대상), 위험등급 경고에 동의하고 진행한 경우 "risk_profile.match.warned"
(구매자 대상)를 함께 발행한다. publish가 없으면(기본값) 발행을
생략한다 — 앱 조립 단계(main.py) 이전의 단위테스트가 이 서비스를
EventBus 없이 그대로 쓸 수 있어야 하기 때문(check_risk_warning과 동일
Optional 패턴).

편차(ADR-2026-08-29 §1): 가격 통화가 플랫폼 내부 크레딧 지갑
(wallet_service.py)으로 바뀌면서 "결제 확인" 중간 상태가 사라졌다 —
지갑 잔액은 구매 시점에 이미 검증된 자금이므로, 구매가 성공하면 그
즉시 payment_status='CONFIRMED'로 기록한다(구 payment_confirmation_
service.py가 담당하던 사후 관리자 확인 단계는 삭제됐다). 리스팅 조회에
`FOR UPDATE`를 걸고 전체를 하나의 트랜잭션으로 묶어, 같은 리스팅에 대한
동시 구매 요청이 잔액 검증을 통과한 뒤 이중으로 차감·정산되는 경쟁을
막는다. 무료 리스팅(price가 NULL)은 지갑을 전혀 건드리지 않는다.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.services.commission import DEFAULT_COMMISSION_RATE, calculate_commission
from src.services.wallet_service import (
    PLATFORM_HOUSE_USER_ID,
    InsufficientBalanceError,
    credit,
    debit,
)

CheckRiskWarningFn = Callable[[UUID, str, str], Awaitable[str | None]]
PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]


async def _no_risk_warning(buyer_user_id: UUID, strategy_id: str, strategy_version: str) -> None:
    return None


class PurchaseError(Exception):
    """FD-13.3 실패 — 라우터가 400/403/404/409로 변환."""


class InsufficientWalletBalanceError(PurchaseError):
    """지갑 잔액 부족 — 라우터가 402로 변환."""


class PurchaseResult(BaseModel):
    purchase_id: int
    status: str
    risk_warning: str | None = None
    platform_commission_amount: Decimal | None = None
    seller_payout_amount: Decimal | None = None


class PurchaseService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        check_risk_warning: CheckRiskWarningFn = _no_risk_warning,
        commission_rate: Decimal = DEFAULT_COMMISSION_RATE,
        publish: PublishFn | None = None,
    ) -> None:
        self._pool = pool
        self._check_risk_warning = check_risk_warning
        self._commission_rate = commission_rate
        self._publish = publish

    async def purchase(
        self,
        buyer_user_id: UUID,
        listing_id: int,
        *,
        risk_warning_acknowledged: bool = False,
    ) -> PurchaseResult:
        warning: str | None = None
        async with self._pool.acquire() as conn, conn.transaction():
            listing = await conn.fetchrow(
                "SELECT * FROM strategy_listings WHERE id = $1 FOR UPDATE", listing_id
            )
            if listing is None:
                raise PurchaseError("존재하지 않는 리스팅입니다.")
            if listing["status"] != "LISTED":
                raise PurchaseError(
                    f"구매할 수 없는 리스팅 상태입니다(현재: {listing['status']})."
                )
            if listing["seller_user_id"] == buyer_user_id:
                raise PurchaseError("본인이 판매 중인 전략은 구매할 수 없습니다.")

            # 전수감사(docs/FULL_AUDIT_2026-09-02.md §2) 반영 — 같은 구매자가
            # 같은 리스팅을 다른 Idempotency-Key로 재요청하면 두 번 차감·정산됐다.
            # 위 FOR UPDATE가 같은 리스팅에 대한 동시 구매를 직렬화하므로 이
            # 조회는 경합에 안전하다. DB UNIQUE(listing_id, buyer_user_id)는
            # 그 위에 덧댄 마지막 방어선이다(아래 UniqueViolationError 처리).
            already_purchased = await conn.fetchval(
                "SELECT id FROM strategy_purchases "
                "WHERE listing_id = $1 AND buyer_user_id = $2",
                listing_id,
                buyer_user_id,
            )
            if already_purchased is not None:
                raise PurchaseError("이미 구매한 리스팅입니다.")

            warning = await self._check_risk_warning(
                buyer_user_id, listing["strategy_id"], listing["strategy_version"]
            )
            if warning is not None and not risk_warning_acknowledged:
                raise PurchaseError(warning)

            price_paid = listing["price"]
            commission_amount, seller_payout_amount = calculate_commission(
                price_paid, self._commission_rate
            )
            commission_rate = self._commission_rate if price_paid is not None else None

            try:
                row = await conn.fetchrow(
                    "INSERT INTO strategy_purchases "
                    "(listing_id, buyer_user_id, price_paid, platform_commission_rate, "
                    "platform_commission_amount, seller_payout_amount, payment_status, "
                    "confirmed_at) "
                    "VALUES ($1, $2, $3, $4, $5, $6, 'CONFIRMED', now()) "
                    "RETURNING id, payment_status",
                    listing_id,
                    buyer_user_id,
                    price_paid,
                    commission_rate,
                    commission_amount,
                    seller_payout_amount,
                )
            except asyncpg.UniqueViolationError as exc:
                raise PurchaseError("이미 구매한 리스팅입니다.") from exc

            if price_paid is not None:
                assert seller_payout_amount is not None  # calculate_commission 불변식
                try:
                    await debit(
                        conn,
                        buyer_user_id,
                        price_paid,
                        "PURCHASE_DEBIT",
                        related_purchase_id=row["id"],
                    )
                except InsufficientBalanceError as exc:
                    raise InsufficientWalletBalanceError(str(exc)) from exc
                await credit(
                    conn,
                    listing["seller_user_id"],
                    seller_payout_amount,
                    "SALE_CREDIT",
                    related_purchase_id=row["id"],
                )
                if commission_amount:
                    await credit(
                        conn,
                        PLATFORM_HOUSE_USER_ID,
                        commission_amount,
                        "COMMISSION_CREDIT",
                        related_purchase_id=row["id"],
                    )

        if self._publish is not None:
            await self._publish(
                "marketplace.purchase.requested",
                {
                    "event_type": "marketplace.purchase.requested",
                    "user_id": str(buyer_user_id),
                    "purchase_id": row["id"],
                    "listing_id": listing_id,
                },
            )
            if warning is not None and risk_warning_acknowledged:
                await self._publish(
                    "risk_profile.match.warned",
                    {
                        "event_type": "risk_profile.match.warned",
                        "user_id": str(buyer_user_id),
                        "reason": warning,
                    },
                )

        return PurchaseResult(
            purchase_id=row["id"],
            status=row["payment_status"],
            risk_warning=warning if risk_warning_acknowledged else None,
            platform_commission_amount=commission_amount,
            seller_payout_amount=seller_payout_amount,
        )
