"""orders

Revision ID: 210cc26533c7
Revises: 0748fc49a05b
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.6.md (Orders, 8.3 Order State Machine)

편차: user_id의 REFERENCES users(user_id)는 11.1에서 ALTER TABLE로 추가한다
(a7c02fa80d22 참조). execution_id 컬럼은 strategy_executions 테이블이 생기는
시점(작업트리 3.14, FD-16 착수 직전)에 별도 ALTER TABLE로 추가한다 — 04번
문서 자체도 이 컬럼을 base DDL이 아니라 후속 ALTER TABLE로 추가한다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "210cc26533c7"
down_revision: str | Sequence[str] | None = "0748fc49a05b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE orders (
            order_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id              UUID NOT NULL,  -- FK: 11.1에서 ALTER TABLE로 추가
            client_order_id      VARCHAR(100) NOT NULL UNIQUE,
            exchange_order_id    VARCHAR(100),
            strategy_id          VARCHAR(100) NOT NULL,
            strategy_version     VARCHAR(20) NOT NULL,
            symbol               VARCHAR(30) NOT NULL,
            exchange             VARCHAR(30) NOT NULL,
            side                 VARCHAR(4) NOT NULL CHECK (side IN ('BUY','SELL')),
            order_type           VARCHAR(10) NOT NULL,
            quantity             NUMERIC(30,10) NOT NULL,
            price                NUMERIC(30,10),
            status               VARCHAR(20) NOT NULL DEFAULT 'CREATED',
            filled_quantity       NUMERIC(30,10) NOT NULL DEFAULT 0,
            average_fill_price    NUMERIC(30,10),
            is_liquidation        BOOLEAN NOT NULL DEFAULT FALSE,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_orders_status ON orders(status)")
    op.execute("CREATE INDEX idx_orders_strategy ON orders(strategy_id, strategy_version)")
    op.execute("CREATE INDEX idx_orders_unknown ON orders(status) WHERE status = 'UNKNOWN'")


def downgrade() -> None:
    op.execute("DROP TABLE orders")
