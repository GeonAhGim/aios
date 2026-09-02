"""FD-13.11(신설) — 마켓플레이스 통화를 플랫폼 내부 크레딧(포인트) 지갑으로.

Spec: 14_marketplace_detailed_v1.1.md §14.1(가격 통화=KRW, 자동 PG 미도입은
의도적 설계, 19장 법률검토 후 진행)의 원칙을 그대로 지키면서 그 위에 내부
지갑 계층을 추가한다. 상세 배경은
ADR-2026-08-29-wallet-marketplace-dual-seller-strategy-authoring.md §1 참조
— 요약하면: 유저간 P2P 거래에서 실제 은행송금을 플랫폼이 건별로 중개하면
전자금융업 등록 이슈가 생기므로, "충전(원화 입금 → 관리자 수동확인, 구
payment_confirmation_service.py와 동일한 패턴)"과 "구매(지갑 잔액 차감,
즉시 정산)"를 분리한다. 1 크레딧 = 1원 고정 — 별도 환율/발행 로직 없음
(11번 §11.1 Money 타입 원칙과 동일하게 KRW 단일 통화 그대로, 표시 단위만
"크레딧"으로 부름).

편차: payment_confirmation_service.py(FD-18.5a/18.5b, 구매 건별 결제확인)는
이 leaf로 완전히 대체되어 삭제됐다 — 구매 시점에 지갑 잔액이 이미
검증되므로 사후 관리자 확인이 필요한 중간 상태(PENDING_PAYMENT)가 더는
발생하지 않는다. 관리자 확인이 필요한 지점은 "충전 요청"으로 옮겨간다.

seller_type='PLATFORM' 리스팅(ADR §2, 동일 커미션 구조로 취급)의 정산
수취인도 이 하우스 계정이다 — 플랫폼이 스스로에게 커미션을 떼는 구조가
되어 실질적으로 판매대금 전액이 이 지갑에 쌓인다(회계상 자연스러움,
purchase_service.py에 별도 분기 불필요).

LC-12(§5.4 3단계) — `debit`/`credit`→`legacy_wallet_bridge`,
`confirm_topup`→`application/topup.post_topup` 위임(상세는 그 모듈들의
docstring). 공개 시그니처·`InsufficientBalanceError`는 불변, 진실은
이제 `ledger_balance`이고 `user_wallets`/`wallet_transactions`는 투영이다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.logging.audit_log import record_audit_log
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.legacy_wallet_bridge import (
    BridgeInsufficientBalanceError,
    bridge_credit,
    bridge_debit,
)
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.topup import post_topup

DEFAULT_PAGE_SIZE = 20

PLATFORM_HOUSE_USER_ID = UUID("00000000-0000-0000-0000-000000000001")
"""마켓플레이스 커미션 수취 + PLATFORM 리스팅 판매자로 쓰는 예약 시스템
계정. db/migrations/versions/e7f8a9b0c1d2_wallet_ledger.py가 동일 UUID로
users/user_wallets 행을 시드한다."""

_WALLET_TX_TYPES = frozenset(
    {
        "TOPUP",
        "PURCHASE_DEBIT",
        "SALE_CREDIT",
        "COMMISSION_CREDIT",
        "REFUND",
        # 레드팀 #41 — 환불 시 판매자 정산·플랫폼 커미션 회수. 이전에는 구매자에게
        # price_paid를 적립만 하고 판매자/하우스에서 회수하지 않아 환불마다
        # 시스템 총잔액이 price_paid만큼 늘어났다(돈이 생성됨).
        "REFUND_SELLER_CLAWBACK",
        "REFUND_COMMISSION_CLAWBACK",
        "REFUND_SHORTFALL_COVER",
    }
)


class InsufficientBalanceError(Exception):
    """잔액 부족 — 호출부(purchase_service 등)가 적절한 HTTP 상태로 변환."""


class WalletTopupError(Exception):
    """충전 요청 처리 실패 — 라우터가 400/404로 변환."""


class WalletBalance(BaseModel):
    user_id: UUID
    balance: Decimal


class WalletTopupRequest(BaseModel):
    id: int
    user_id: UUID
    requested_amount: Decimal
    status: str
    requested_at: datetime
    confirmed_at: datetime | None
    confirmed_by: UUID | None = None


class WalletTopupPage(BaseModel):
    items: list[WalletTopupRequest]
    total: int
    page: int
    page_size: int


class WalletTopupConfirmResult(BaseModel):
    id: int
    status: str
    balance_after: Decimal | None
    confirmed_at: datetime | None


async def debit(
    conn: asyncpg.Connection,
    user_id: UUID,
    amount: Decimal,
    tx_type: str,
    *,
    related_purchase_id: int | None = None,
) -> Decimal:
    """호출부의 `conn.transaction()` 안에서만 호출한다. 잔액부족 검증은
    `post_entry`(LC-9)의 `FOR UPDATE`+`allow_negative=False`가 겸한다."""
    assert tx_type in _WALLET_TX_TYPES, f"알 수 없는 거래 유형: {tx_type}"
    try:
        return await bridge_debit(
            conn, user_id, amount, tx_type, related_purchase_id=related_purchase_id
        )
    except BridgeInsufficientBalanceError as exc:
        raise InsufficientBalanceError("지갑 잔액이 부족합니다.") from exc


async def credit(
    conn: asyncpg.Connection,
    user_id: UUID,
    amount: Decimal,
    tx_type: str,
    *,
    related_purchase_id: int | None = None,
) -> Decimal:
    """지갑이 아직 없는 사용자(가입 후 최초 충전/환불)는 투영에서 생성한다."""
    assert tx_type in _WALLET_TX_TYPES, f"알 수 없는 거래 유형: {tx_type}"
    return await bridge_credit(
        conn, user_id, amount, tx_type, related_purchase_id=related_purchase_id
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WalletService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._journal = PostgresJournalRepository(pool)
        self._balances = PostgresBalanceRepository(pool)
        self._audit = PostgresAuditEventRepository(pool)

    async def get_balance(self, user_id: UUID) -> WalletBalance:
        async with self._pool.acquire() as conn:
            balance = await conn.fetchval(
                "SELECT balance FROM user_wallets WHERE user_id = $1", user_id
            )
        return WalletBalance(
            user_id=user_id, balance=balance if balance is not None else Decimal("0")
        )

    async def request_topup(self, user_id: UUID, amount: Decimal) -> WalletTopupRequest:
        if amount <= 0:
            raise WalletTopupError("충전 금액은 0보다 커야 합니다.")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO wallet_topup_requests (user_id, requested_amount) "
                "VALUES ($1, $2) RETURNING *",
                user_id, amount,
            )
        return WalletTopupRequest(**dict(row))

    async def list_pending_topups(
        self, *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE
    ) -> WalletTopupPage:
        async with self._pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM wallet_topup_requests WHERE status = 'PENDING'"
            )
            rows = await conn.fetch(
                "SELECT * FROM wallet_topup_requests WHERE status = 'PENDING' "
                "ORDER BY requested_at ASC LIMIT $1 OFFSET $2",
                page_size, (page - 1) * page_size,
            )
        return WalletTopupPage(
            items=[WalletTopupRequest(**dict(row)) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def confirm_topup(
        self,
        topup_id: int,
        admin_user_id: UUID,
        *,
        idempotency_key: str,  # noqa: ARG002 — DB 상태 자체가 멱등성 근거(아래 참조)
    ) -> WalletTopupConfirmResult:
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchrow(
                "SELECT user_id, requested_amount, status, confirmed_at "
                "FROM wallet_topup_requests WHERE id = $1",
                topup_id,
            )
            if current is None:
                raise WalletTopupError("존재하지 않는 충전 요청입니다.")

            if current["status"] == "CONFIRMED":
                return WalletTopupConfirmResult(
                    id=topup_id, status="CONFIRMED", balance_after=None,
                    confirmed_at=current["confirmed_at"],
                )

            updated = await conn.fetchrow(
                "UPDATE wallet_topup_requests SET status = 'CONFIRMED', confirmed_at = now(), "
                "confirmed_by = $2 WHERE id = $1 AND status = 'PENDING' RETURNING confirmed_at",
                topup_id, admin_user_id,
            )
            if updated is None:
                raise WalletTopupError("이미 다른 관리자가 처리했습니다(동시 처리 충돌).")

            balance_after = await post_topup(
                conn, topup_id, current["user_id"], current["requested_amount"], admin_user_id,
                journal=self._journal, balances=self._balances, audit=self._audit, clock=_utcnow,
            )

            await record_audit_log(
                conn, actor_agent=str(admin_user_id), action_type="wallet.topup.confirmed",
                decision_data={
                    "topup_id": topup_id, "user_id": str(current["user_id"]),
                    "amount": str(current["requested_amount"]),
                },
                target_type="wallet_topup_request", target_id=str(topup_id),
            )

        return WalletTopupConfirmResult(
            id=topup_id, status="CONFIRMED", balance_after=balance_after,
            confirmed_at=updated["confirmed_at"],
        )
