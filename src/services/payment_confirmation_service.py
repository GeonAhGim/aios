"""18.5a/18.5b — 결제 대기 목록 조회 + 결제 확인 처리 (PaymentConfirmationService).

Spec: 기능설계문서_v1.20.md#FD-18.5a/FD-18.5b, 15번 §15.1, 8.10

PENDING_PAYMENT → CONFIRMED 전이 자체가 FD-13.4/13.5(실행 연동)의 실행
권한 부여 트리거다 — StrategyAccessService.can_access()(13.5)가 이미
payment_status='CONFIRMED'를 조건으로 접근을 판정하도록 만들어져
있었으므로, 여기서 상태만 실제로 바꾸면 구매자의 실행 접근권한이 그
즉시 열린다(추가 배선 불필요, "고아 컬럼"이 마침내 값을 바꿀 방법을
갖게 됨).

멱등성: Idempotency-Key 헤더 값 자체를 별도로 저장·대조하지 않는다 —
payment_status가 이미 CONFIRMED인 건에 대한 재확인 시도는 (키가
달라도) UPDATE ... WHERE payment_status='PENDING_PAYMENT'가 매칭되는
행이 없어 자연히 아무 것도 바꾸지 않고 현재 상태를 그대로 반환한다.
DB 상태 자체가 멱등성의 근거이므로 audit_log도 실제 전이가 일어난
최초 1회만 기록된다.

금전 상태를 바꾸는 운영자 액션이라 8.10 원칙상 audit_log 기록 필수
(18.2/18.4와 동일하게 record_audit_log 재사용). FD-17
marketplace.payment.confirmed 이벤트 발행은 EventBus 배선이 앱 조립
단계(16번)에 있어 optional publish DI 콜백으로 남겨둔다.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.logging.audit_log import record_audit_log

DEFAULT_PAGE_SIZE = 20

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]


class PaymentConfirmationError(Exception):
    """FD-18.5b 실패 — 라우터가 404로 변환."""


class PendingPayment(BaseModel):
    purchase_id: int
    buyer_user_id: UUID
    strategy_id: str
    strategy_version: str
    price_paid: Decimal | None
    purchased_at: datetime


class PendingPaymentPage(BaseModel):
    items: list[PendingPayment]
    total: int
    page: int
    page_size: int


class PaymentConfirmationResult(BaseModel):
    purchase_id: int
    payment_status: str
    confirmed_at: datetime | None


class PaymentConfirmationService:
    def __init__(self, pool: asyncpg.Pool, *, publish: PublishFn | None = None) -> None:
        self._pool = pool
        self._publish = publish

    async def list_pending_payments(
        self, *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE
    ) -> PendingPaymentPage:
        async with self._pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM strategy_purchases WHERE payment_status = 'PENDING_PAYMENT'"
            )
            rows = await conn.fetch(
                """
                SELECT p.id AS purchase_id, p.buyer_user_id, p.price_paid, p.purchased_at,
                       l.strategy_id, l.strategy_version
                FROM strategy_purchases p
                JOIN strategy_listings l ON l.id = p.listing_id
                WHERE p.payment_status = 'PENDING_PAYMENT'
                ORDER BY p.purchased_at ASC
                LIMIT $1 OFFSET $2
                """,
                page_size,
                (page - 1) * page_size,
            )
        return PendingPaymentPage(
            items=[PendingPayment(**dict(row)) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def confirm_payment(
        self,
        purchase_id: int,
        admin_user_id: UUID,
        *,
        idempotency_key: str,  # noqa: ARG002 — DB 상태 자체가 멱등성 근거(모듈 docstring 참조)
    ) -> PaymentConfirmationResult:
        newly_confirmed = False
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchrow(
                "SELECT payment_status, confirmed_at FROM strategy_purchases WHERE id = $1",
                purchase_id,
            )
            if current is None:
                raise PaymentConfirmationError("존재하지 않는 구매 건입니다.")

            if current["payment_status"] == "CONFIRMED":
                return PaymentConfirmationResult(
                    purchase_id=purchase_id,
                    payment_status="CONFIRMED",
                    confirmed_at=current["confirmed_at"],
                )

            row = await conn.fetchrow(
                "UPDATE strategy_purchases SET payment_status = 'CONFIRMED', confirmed_at = now() "
                "WHERE id = $1 AND payment_status = 'PENDING_PAYMENT' "
                "RETURNING payment_status, confirmed_at",
                purchase_id,
            )
            newly_confirmed = True

            await record_audit_log(
                conn,
                actor_agent=str(admin_user_id),
                action_type="payment.confirmed",
                decision_data={
                    "purchase_id": purchase_id,
                    "previous_status": "PENDING_PAYMENT",
                    "new_status": "CONFIRMED",
                },
                target_type="strategy_purchase",
                target_id=str(purchase_id),
            )

        if newly_confirmed and self._publish is not None:
            await self._publish("marketplace.payment.confirmed", {"purchase_id": purchase_id})

        return PaymentConfirmationResult(
            purchase_id=purchase_id,
            payment_status=row["payment_status"],
            confirmed_at=row["confirmed_at"],
        )
