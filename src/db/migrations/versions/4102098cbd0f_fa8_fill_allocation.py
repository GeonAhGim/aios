"""FA-8 — `fill_allocation` 테이블: 체결 → sub_account 배분 결과 영속화.

Revision ID: 4102098cbd0f
Revises: 963d5f3cfb1b
Create Date: 2026-09-07 00:00:00.000000

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-8
(§9 표·§2.1 allocation 행·§4 FA-A3).

이 테이블은 "체결 → sub_account 배분"의 실제 사실(quantity·average_price)을
담는 실체 기록이다 — decision(task-1796)이 금지한 "자체 중복 검사 테이블"이
아니다(그건 멱등 lookup 전용 보조 테이블을 뜻하고, 멱등은 여기서 LC-3
`post_entry`의 `idempotency_key`를 그대로 재사용한다 — `application/
allocate_fills.py` 참고). `UNIQUE(order_id, sub_account_id)`는 이 실체
기록 자체의 무결성 제약(같은 주문의 같은 sub_account는 한 번만 배분)이지,
별도 중복 검사용 테이블이 아니다.

`fills`·`pos_journal`·`ledger_journal_entry`와 같은 이유로 append-only —
배분은 사실이 확정된 뒤의 기록이라 정정은 새 행(FA-11 역분개 패턴)으로
하고 기존 행을 UPDATE하지 않는다(`core/db/append_only.worm_sql` 재사용,
새 WORM 구현 금지).

RLS 미도입: `order_id`(→`orders`)·`sub_account_id`(→`sub_account`) 양쪽
FK로 이미 간접 격리된다(789c138f13fe·963d5f3cfb1b과 동일 원칙 — 부모로
간접 격리되는 자식 테이블은 RLS 제외).
"""
from collections.abc import Sequence

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql

# revision identifiers, used by Alembic.
revision: str = "4102098cbd0f"
down_revision: str | Sequence[str] | None = "963d5f3cfb1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "fill_allocation"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {_TABLE} (
            id              BIGSERIAL PRIMARY KEY,
            order_id        UUID NOT NULL REFERENCES orders(order_id),
            sub_account_id  UUID NOT NULL REFERENCES sub_account(sub_account_id),
            tenant_id       UUID NOT NULL,
            fund_id         UUID NOT NULL REFERENCES fund(fund_id),
            portfolio_id    UUID NOT NULL REFERENCES portfolio(portfolio_id),
            quantity        NUMERIC(30,10) NOT NULL CHECK (quantity > 0),
            average_price   NUMERIC(30,10) NOT NULL CHECK (average_price > 0),
            ledger_entry_id UUID NOT NULL REFERENCES ledger_journal_entry(entry_id),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (order_id, sub_account_id)
        )
        """
    )
    op.execute(f"CREATE INDEX idx_{_TABLE}_sub_account_id ON {_TABLE}(sub_account_id)")
    op.execute(f"CREATE INDEX idx_{_TABLE}_fund_id ON {_TABLE}(fund_id)")
    for statement in worm_sql(_TABLE):
        op.execute(statement)


def downgrade() -> None:
    for statement in worm_drop_sql(_TABLE):
        op.execute(statement)
    op.execute(f"DROP INDEX IF EXISTS idx_{_TABLE}_fund_id")
    op.execute(f"DROP INDEX IF EXISTS idx_{_TABLE}_sub_account_id")
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
