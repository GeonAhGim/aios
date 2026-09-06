"""FA-3 — 소급 마이그레이션 A: orders·fills에 fund_id/portfolio_id 컬럼+FK+백필.

Revision ID: 789c138f13fe
Revises: c9f4e2a1b6d7
Create Date: 2026-09-06 23:14:37.203216

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-3
(§9 FA-3 DoD, §3 계약, §4 FA-A1). PM 2026-09-06 3차·최종 decision
(task-1709) — 선택지 B(armed-cutover) 채택: 착수 시점 `alembic heads`가
단일(c9f4e2a1b6d7, task-1718)임을 확인하고 그 값을 down_revision으로 썼다.

이 리프에서 NOT NULL을 강제하지 않는다 — `legal_entity`/`fund`/
`portfolio` 부트스트랩에 필요한 jurisdiction/region_tag/base_currency/
venue_account_ref의 데이터 원천이 기존 사용자에게 없다(task-1709 재시도
2 에스컬레이션). jurisdiction=UNVERIFIED·currency=USDT 같은 임의
placeholder는 PM이 기각했다(선택지 C). NOT NULL 승격과 잔여 백필은
FA-5(resolve_context) 완료 후 별도 리프.

백필 규칙: FA-1 `domain/defaults.py`의 결정론 규칙(`default_fund_id`/
`default_portfolio_id`, user_id 단일 인자의 UUIDv5)만 재사용한다 — 이
마이그레이션 안에서 새 규칙을 짜지 않는다. orders.user_id마다 그 규칙으로
fund_id/portfolio_id를 계산하고, 그 id의 fund/portfolio 행이 **이미
존재하는 경우에만**(= 그 사용자가 FA-2 경로로 이미 부트스트랩됨) 백필한다.
값이 나오지 않는 행은 NULL로 남긴다.

`fund_id`/`portfolio_id`가 pure Python 함수(UUIDv5)이므로 SQL만으로
재현하려면 `uuid-ossp`의 `uuid_generate_v5`(현재 DB에 미설치, `pgcrypto`의
`gen_random_uuid()`와는 다른 확장)에 의존해야 한다 — 대신 이 마이그레이션은
Python에서 직접 `default_fund_id`/`default_portfolio_id`를 호출해
파라미터 바인딩된 UPDATE를 사용자별로 실행한다(마이그레이션은 1회성이며
사용자 수가 SQL 함수 이식 리스크를 감수할 만큼 크지 않다).

**fills는 백필하지 않는다(구현 중 발견, decision 시점에 알려지지 않은
사실)**: `fills`는 `073beca589d5`가 이미 `worm_sql("fills")`로 append-only
트리거를 걸었다 — `BEFORE UPDATE OR DELETE ... RAISE EXCEPTION`이 역할과
무관하게 모든 UPDATE를 거부한다(마이그레이터 role도 예외 없음). 즉 기존
fills 행은 물리적으로 다시 쓸 수 없다 — §3 "WORM 트리거가 이미 걸린
append-only 테이블은 물리적으로 UPDATE가 불가하다"는 서술이 fills 자신의
백필에도 그대로 적용된다. 트리거를 일시 해제하고 백필한 뒤 복구하는
방법은 append-only 보장을 마이그레이션 한 번을 위해 깨는 것이라 채택하지
않는다. 컬럼/FK/인덱스는 추가한다 — 새 체결(FA-5/FA-8 이후 쓰기 경로가
INSERT 시점에 값을 채움, 이 리프 범위 밖)부터는 값이 채워지고, 기존 행은
영구히 NULL로 남는다(잔여 건수를 테스트로 단언한다).
"""
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from src.foundation.entities.domain.defaults import default_fund_id, default_portfolio_id

# revision identifiers, used by Alembic.
revision: str = "789c138f13fe"
down_revision: str | Sequence[str] | None = "c9f4e2a1b6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE orders ADD COLUMN fund_id UUID REFERENCES fund(fund_id)")
    op.execute("ALTER TABLE orders ADD COLUMN portfolio_id UUID REFERENCES portfolio(portfolio_id)")
    op.execute("CREATE INDEX idx_orders_fund_id ON orders(fund_id) WHERE fund_id IS NOT NULL")
    op.execute(
        "CREATE INDEX idx_orders_portfolio_id ON orders(portfolio_id) "
        "WHERE portfolio_id IS NOT NULL"
    )

    op.execute("ALTER TABLE fills ADD COLUMN fund_id UUID REFERENCES fund(fund_id)")
    op.execute("ALTER TABLE fills ADD COLUMN portfolio_id UUID REFERENCES portfolio(portfolio_id)")
    op.execute("CREATE INDEX idx_fills_fund_id ON fills(fund_id) WHERE fund_id IS NOT NULL")
    op.execute(
        "CREATE INDEX idx_fills_portfolio_id ON fills(portfolio_id) WHERE portfolio_id IS NOT NULL"
    )

    _backfill_orders()


def _backfill_orders() -> None:
    bind = op.get_bind()
    user_ids = [
        row[0] for row in bind.execute(text("SELECT DISTINCT user_id FROM orders")).fetchall()
    ]
    for user_id in user_ids:
        fund_id = default_fund_id(user_id)
        portfolio_id = default_portfolio_id(user_id)
        bind.execute(
            text(
                "UPDATE orders SET fund_id = :fund_id, portfolio_id = :portfolio_id "
                "WHERE user_id = :user_id "
                "AND EXISTS (SELECT 1 FROM fund WHERE fund_id = :fund_id) "
                "AND EXISTS ("
                "  SELECT 1 FROM portfolio "
                "  WHERE portfolio_id = :portfolio_id AND fund_id = :fund_id"
                ")"
            ),
            {"fund_id": fund_id, "portfolio_id": portfolio_id, "user_id": user_id},
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_fills_portfolio_id")
    op.execute("DROP INDEX IF EXISTS idx_fills_fund_id")
    op.execute("ALTER TABLE fills DROP COLUMN portfolio_id")
    op.execute("ALTER TABLE fills DROP COLUMN fund_id")

    op.execute("DROP INDEX IF EXISTS idx_orders_portfolio_id")
    op.execute("DROP INDEX IF EXISTS idx_orders_fund_id")
    op.execute("ALTER TABLE orders DROP COLUMN portfolio_id")
    op.execute("ALTER TABLE orders DROP COLUMN fund_id")
