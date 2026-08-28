"""strategy_listings / strategy_purchases

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-28 00:26:09.061936

Spec: 13_multi_tenancy_auth_v1.4.md#§13.5. 작업트리 13.1.

편차: §13.5 DDL 원문은 platform_commission_rate/platform_commission_amount/
seller_payout_amount를 strategy_purchases 기본 DDL에 "v1.1 병합"으로
적어뒀지만, 10번 문서(작업트리) 자체가 이 세 컬럼을 별도 리프
"3.13(13.7 이후)"로 명시적으로 분리해뒀다 — 11.1에서 risk_profile을
동일한 이유로 users 기본 DDL에서 제외했던 것과 같은 원칙을 그대로
적용해, 여기서도 제외한다(FD-13.7 착수 시 ALTER TABLE로 추가).

seller_suspended(users 컬럼, §14.5.3 분쟁 판매자 제재)도 이 섹션(13번)
어디에서도 쓰이지 않고 FD-18.4(판매자 정지 API)에서 처음 필요해지므로
같은 원칙으로 지금 만들지 않는다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE strategy_listings (
            id                  BIGSERIAL PRIMARY KEY,
            strategy_id         VARCHAR(100) NOT NULL,
            strategy_version    VARCHAR(20) NOT NULL,
            seller_user_id      UUID NOT NULL REFERENCES users(user_id),
            price               NUMERIC(20,2),
            status              VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
                CHECK (status IN ('DRAFT','PENDING_VERIFICATION','LISTED','DELISTED')),
            FOREIGN KEY (strategy_id, strategy_version)
                REFERENCES strategies(strategy_id, version),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_strategy_listings_seller ON strategy_listings(seller_user_id)"
    )
    op.execute(
        """
        CREATE TABLE strategy_purchases (
            id                  BIGSERIAL PRIMARY KEY,
            listing_id          BIGINT NOT NULL REFERENCES strategy_listings(id),
            buyer_user_id       UUID NOT NULL REFERENCES users(user_id),
            purchased_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            price_paid          NUMERIC(20,2),
            payment_status      VARCHAR(20) NOT NULL DEFAULT 'PENDING_PAYMENT'
                CHECK (payment_status IN ('PENDING_PAYMENT','CONFIRMED'))
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_strategy_purchases_buyer ON strategy_purchases(buyer_user_id)"
    )
    op.execute(
        "CREATE INDEX idx_strategy_purchases_listing ON strategy_purchases(listing_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE strategy_purchases")
    op.execute("DROP TABLE strategy_listings")
