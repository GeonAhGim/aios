"""strategy_purchases 중개수수료 컬럼 추가

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-28 00:26:09.061936

Spec: 기능설계문서_v1.20.md#FD-13.7, 13_multi_tenancy_auth_v1.4.md#§13.5

13.1에서 명시적으로 미뤄뒀던 3개 컬럼(3.13, "13.7 이후") — 지금 착수.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE strategy_purchases ADD COLUMN platform_commission_rate NUMERIC(5,4)"
    )
    op.execute(
        "ALTER TABLE strategy_purchases ADD COLUMN platform_commission_amount NUMERIC(20,2)"
    )
    op.execute(
        "ALTER TABLE strategy_purchases ADD COLUMN seller_payout_amount NUMERIC(20,2)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE strategy_purchases DROP COLUMN seller_payout_amount")
    op.execute("ALTER TABLE strategy_purchases DROP COLUMN platform_commission_amount")
    op.execute("ALTER TABLE strategy_purchases DROP COLUMN platform_commission_rate")
