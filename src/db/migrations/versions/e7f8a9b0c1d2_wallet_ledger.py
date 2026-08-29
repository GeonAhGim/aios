"""user_wallets / wallet_transactions / wallet_topup_requests — 마켓플레이스
내부 크레딧 지갑

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-08-29 00:00:00.000000

Spec: 14_marketplace_detailed_v1.1.md §14.1(가격 통화=KRW 단일통화, 자동 PG
미도입은 19장 법률검토 전까지 의도적 설계) 원칙 위에, 사용자 결정
(ADR-2026-08-29-wallet-marketplace-dual-seller-strategy-authoring.md §1)에
따라 내부 크레딧 지갑 계층을 신설한다 — 유저간 P2P 거래에서 플랫폼이
실제 은행송금을 건별로 중개하면 전자금융업 등록 문제가 생기므로,
"충전(수동확인)"과 "구매(지갑 잔액 즉시 차감)"를 분리해 자동 PG 없이도
구매 시점에 결제가 확정되게 한다. 1 크레딧 = 1원(고정, 별도 환전 없음).

house 계정(PLATFORM_HOUSE_USER_ID, src/services/wallet_service.py와 동일
UUID)을 함께 시드한다 — 커미션 수취 + seller_type='PLATFORM' 리스팅의
판매자 역할을 겸한다. 실제 로그인이 불가능하도록 password_hash에 bcrypt
해시 형식이 아닌 사용 불가 마커를 넣는다(users.password_hash NOT NULL
제약 충족용, auth_service의 해시 검증이 이 값으로 절대 통과하지 못함).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "d6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORM_HOUSE_USER_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO users (user_id, email, password_hash, display_name, is_platform_admin)
        VALUES ('{PLATFORM_HOUSE_USER_ID}', 'platform-house@aios.internal',
                'DISABLED_NO_LOGIN', 'AIOS 플랫폼', FALSE)
        ON CONFLICT (user_id) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE user_wallets (
            user_id     UUID PRIMARY KEY REFERENCES users(user_id),
            balance     NUMERIC(20,2) NOT NULL DEFAULT 0 CHECK (balance >= 0),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"INSERT INTO user_wallets (user_id, balance) VALUES ('{PLATFORM_HOUSE_USER_ID}', 0)"
    )
    op.execute(
        """
        CREATE TABLE wallet_transactions (
            id                    BIGSERIAL PRIMARY KEY,
            user_id               UUID NOT NULL REFERENCES users(user_id),
            tx_type               VARCHAR(20) NOT NULL
                CHECK (tx_type IN ('TOPUP','PURCHASE_DEBIT','SALE_CREDIT',
                                    'COMMISSION_CREDIT','REFUND')),
            amount                NUMERIC(20,2) NOT NULL,
            balance_after         NUMERIC(20,2) NOT NULL,
            related_purchase_id   BIGINT REFERENCES strategy_purchases(id),
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_wallet_transactions_user ON wallet_transactions(user_id)")
    op.execute(
        """
        CREATE TABLE wallet_topup_requests (
            id                  BIGSERIAL PRIMARY KEY,
            user_id             UUID NOT NULL REFERENCES users(user_id),
            requested_amount    NUMERIC(20,2) NOT NULL CHECK (requested_amount > 0),
            status              VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING','CONFIRMED')),
            requested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            confirmed_at        TIMESTAMPTZ,
            confirmed_by        UUID REFERENCES users(user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_wallet_topup_requests_user ON wallet_topup_requests(user_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE wallet_topup_requests")
    op.execute("DROP TABLE wallet_transactions")
    op.execute("DROP TABLE user_wallets")
    op.execute(f"DELETE FROM users WHERE user_id = '{PLATFORM_HOUSE_USER_ID}'")
