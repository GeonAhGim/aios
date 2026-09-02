"""wallet_transactions.tx_type — 환불 회수 유형 3종 추가 (레드팀 #41)

Revision ID: d2c8b1e4f6a0
Revises: 5ed4921f9873
Create Date: 2026-09-03 01:00:00

환불이 구매자 적립만 하고 판매자 정산·커미션을 회수하지 않아 환불마다
시스템 총잔액이 price_paid만큼 증가하던 결함(docs/specs/L4_market_data_
positions_ledger_v1.0.md R1)의 수정에 필요한 거래 유형을 CHECK 제약에 추가.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "d2c8b1e4f6a0"
down_revision: str | None = "5ed4921f9873"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "('TOPUP','PURCHASE_DEBIT','SALE_CREDIT','COMMISSION_CREDIT','REFUND')"
_NEW = (
    "('TOPUP','PURCHASE_DEBIT','SALE_CREDIT','COMMISSION_CREDIT','REFUND',"
    "'REFUND_SELLER_CLAWBACK','REFUND_COMMISSION_CLAWBACK','REFUND_SHORTFALL_COVER')"
)


def upgrade() -> None:
    op.execute("ALTER TABLE wallet_transactions DROP CONSTRAINT wallet_transactions_tx_type_check")
    # 신규 유형명이 VARCHAR(20)을 넘는다(최대 26자) — 확장.
    op.execute("ALTER TABLE wallet_transactions ALTER COLUMN tx_type TYPE VARCHAR(40)")
    op.execute(
        "ALTER TABLE wallet_transactions ADD CONSTRAINT wallet_transactions_tx_type_check "
        f"CHECK (tx_type IN {_NEW})"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE wallet_transactions DROP CONSTRAINT wallet_transactions_tx_type_check")
    op.execute("ALTER TABLE wallet_transactions ALTER COLUMN tx_type TYPE VARCHAR(20)")
    op.execute(
        "ALTER TABLE wallet_transactions ADD CONSTRAINT wallet_transactions_tx_type_check "
        f"CHECK (tx_type IN {_OLD})"
    )
