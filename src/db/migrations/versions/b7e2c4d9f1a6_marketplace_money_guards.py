"""marketplace money guards — price CHECK, purchase UNIQUE, refunded_at

Revision ID: b7e2c4d9f1a6
Revises: f2b8e5d1a734
Create Date: 2026-09-02 23:10:00

Spec: docs/FULL_AUDIT_2026-09-02.md §2 (P0 결함), 14_marketplace_detailed_v1.1.md

전수감사에서 확인된 "돈이 새는" 경로 세 가지를 DB 제약으로 막는다. 서비스
계층(listing_service / purchase_service / dispute_resolution_service)에도
같은 검사가 있지만, 이 제약은 코드 경로를 우회하는 어떤 쓰기에도 마지막
방어선으로 작동한다.

1. strategy_listings.price >= 0 — 음수 가격이 구매 시 지갑 증액이 되는 경로 차단.
2. UNIQUE(listing_id, buyer_user_id) — 같은 구매자의 같은 리스팅 이중 구매·
   이중 정산 차단. 환불된 구매도 행이 남으므로(refunded_at) 재구매는 불가 —
   환불은 DELISTED와 함께만 일어나므로 재구매가 필요한 경우 자체가 없다.
3. strategy_purchases.refunded_at — 환불 1회 보장의 조건부 UPDATE 대상.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e2c4d9f1a6"
down_revision: str | None = "f2b8e5d1a734"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE strategy_listings "
        "ADD CONSTRAINT ck_strategy_listings_price_nonnegative "
        "CHECK (price IS NULL OR price >= 0)"
    )
    op.execute("ALTER TABLE strategy_purchases ADD COLUMN refunded_at TIMESTAMPTZ")
    op.execute(
        "CREATE UNIQUE INDEX uq_strategy_purchases_listing_buyer "
        "ON strategy_purchases (listing_id, buyer_user_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_strategy_purchases_listing_buyer")
    op.execute("ALTER TABLE strategy_purchases DROP COLUMN refunded_at")
    op.execute(
        "ALTER TABLE strategy_listings DROP CONSTRAINT ck_strategy_listings_price_nonnegative"
    )
