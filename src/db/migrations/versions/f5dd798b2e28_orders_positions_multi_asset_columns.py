"""orders_positions_multi_asset_columns

Revision ID: f5dd798b2e28
Revises: 9ec8a1ee28d7
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.7.md (다자산군 확장, ADR-2026-08-28)

이미 적용된 3.6/3.7(orders/positions) 마이그레이션을 되돌리지 않고, 새
마이그레이션으로 컬럼을 추가한다(10번 문서 3.20 리프 — 이력은 항상
순방향으로만 추가하는 원칙). 전부 nullable — 기존 크립토 행은 그대로 NULL.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5dd798b2e28"
down_revision: str | None = "9ec8a1ee28d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_COLUMNS_SQL = """
    ADD COLUMN asset_class VARCHAR(20),
    ADD COLUMN option_type VARCHAR(4) CHECK (option_type IN ('CALL','PUT')),
    ADD COLUMN strike_price NUMERIC(30,10),
    ADD COLUMN expiry_date TIMESTAMPTZ,
    ADD COLUMN contract_multiplier NUMERIC(20,4),
    ADD COLUMN underlying_symbol VARCHAR(30)
"""


def upgrade() -> None:
    op.execute(f"ALTER TABLE orders {_NEW_COLUMNS_SQL}")
    op.execute(f"ALTER TABLE positions {_NEW_COLUMNS_SQL}")
    op.execute(
        "CREATE INDEX idx_orders_asset_class ON orders(asset_class) "
        "WHERE asset_class IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX idx_orders_asset_class")
    for table in ("orders", "positions"):
        op.execute(
            f"""
            ALTER TABLE {table}
                DROP COLUMN asset_class,
                DROP COLUMN option_type,
                DROP COLUMN strike_price,
                DROP COLUMN expiry_date,
                DROP COLUMN contract_multiplier,
                DROP COLUMN underlying_symbol
            """
        )
