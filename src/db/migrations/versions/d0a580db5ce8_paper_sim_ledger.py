"""paper_sim_ledger — L4-23.

Revision ID: d0a580db5ce8
Revises: 073beca589d5
Create Date: 2026-09-06 18:33:24.566506

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-F(paper 시뮬레이터
표), §2-G(`<rev3>` 행), §5.1("paper_sim 잔고" 조건부 UPDATE 행), §9 L4-23.

두 테이블만 신규(스펙 §2-G 상한 100줄 그대로) — `paper_sim_accounts`(잔고)와
`paper_sim_orders`(venue측 주문, 체결 이력은 `fills` JSONB 컬럼에 append).
`fills`/`provider_event_inbox`(073beca589d5, L4-06)를 재사용하지 않는 이유 —
그 두 테이블은 OMS `orders.order_id` FK로 내부 주문에 묶이는데,
`paper_sim_orders`는 OMS가 아직 그 주문을 모르는 시점(venue 측 1차 기록)에도
존재해야 한다(§6 F17 DROP 경로 — 응답은 유실돼도 venue측 진실은 남아야
`unknown_resolver`가 나중에 조회로 해소할 수 있다). 이 파일은 그 venue측
원장이고, OMS inbox로의 흡수는 L4-16/20(아직 없음) 몫이다.

WORM 미적용 — 두 테이블 모두 잔고 증감·주문 상태 전이로 UPDATE가 필수라
append-only가 아니다. 073beca589d5의 `order_command_outbox`/
`provider_event_inbox`와 같은 관례로 `aios_app`에 SELECT/INSERT/UPDATE만
GRANT하고 DELETE는 주지 않는다(방어 심화, L0-3/L0-5 관례).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d0a580db5ce8"
down_revision: str | Sequence[str] | None = "073beca589d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"

_ACCOUNTS_DDL = """
CREATE TABLE paper_sim_accounts (
    account_id UUID NOT NULL,
    asset VARCHAR(20) NOT NULL,
    total NUMERIC(30,10) NOT NULL DEFAULT 0,
    available NUMERIC(30,10) NOT NULL DEFAULT 0,
    used_margin NUMERIC(30,10) NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, asset),
    CHECK (total >= 0 AND available >= 0 AND available <= total)
)"""

_ORDERS_DDL = """
CREATE TABLE paper_sim_orders (
    order_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL,
    client_order_id VARCHAR(100) NOT NULL,
    symbol VARCHAR(30) NOT NULL,
    side VARCHAR(4) NOT NULL CHECK (side IN ('BUY','SELL')),
    order_type VARCHAR(10) NOT NULL CHECK (order_type IN ('MARKET','LIMIT')),
    quantity NUMERIC(30,10) NOT NULL,
    price NUMERIC(30,10),
    status VARCHAR(20) NOT NULL DEFAULT 'ACKNOWLEDGED',
    filled_quantity NUMERIC(30,10) NOT NULL DEFAULT 0,
    average_fill_price NUMERIC(30,10),
    fee_total NUMERIC(30,10) NOT NULL DEFAULT 0,
    fee_currency VARCHAR(10),
    fills JSONB NOT NULL DEFAULT '[]'::jsonb,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, client_order_id),
    CHECK (filled_quantity >= 0 AND filled_quantity <= quantity)
)"""


def upgrade() -> None:
    op.execute(_ACCOUNTS_DDL)
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON paper_sim_accounts TO {_APP_ROLE}")
    op.execute("REVOKE DELETE ON paper_sim_accounts FROM PUBLIC")

    op.execute(_ORDERS_DDL)
    op.execute("CREATE INDEX idx_paper_sim_orders_account ON paper_sim_orders(account_id)")
    op.execute(
        "CREATE INDEX idx_paper_sim_orders_open ON paper_sim_orders(account_id, symbol) "
        "WHERE status IN ('ACKNOWLEDGED','PARTIALLY_FILLED','UNKNOWN')"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON paper_sim_orders TO {_APP_ROLE}")
    op.execute("REVOKE DELETE ON paper_sim_orders FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS paper_sim_orders")
    op.execute("DROP TABLE IF EXISTS paper_sim_accounts")
