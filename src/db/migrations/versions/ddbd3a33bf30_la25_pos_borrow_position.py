"""LA-25 — pos_borrow_position(공매도 차입·마진 영속화) 신설.

Revision ID: ddbd3a33bf30
Revises: 47ec4b178f54
Create Date: 2026-09-07 01:00:00.000000

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9 LA-25,
ADR-2026-09-06-G §9. `src/foundation/positions/domain/borrow.py`
(task-1752 1차 리프, e31a63b)의 `BorrowPosition` 값 객체를 영속화하는
테이블. 그 리프의 docstring이 예고한 대로 이 리프는 물리 스키마만
만든다 — 리포지토리 어댑터·주문 경로 locate 게이트 배선은 후속 리프다.

parent 결정(task-1752 decision): 착수 시점 `alembic heads`가 단일
(`47ec4b178f54`)임을 확인하고 그대로 `down_revision`으로 쓴다. FA-0a
배치 B(task-1987, positions 계열 tenant_id FK를 users에서 tenant로
전환)는 아직 진행 중이라 `pos_account`/`pos_snapshot`은 여전히
`tenant_id UUID REFERENCES users(user_id)`다 — 이 테이블은 그 레거시를
잇지 않고 신설 테이블답게 처음부터 정확한 대상인 `tenant(id)`를
참조한다(레거시 교정은 이 리프의 몫이 아니다). `position_key`는
`pos_snapshot(position_key)`를 FK로 걸지 않는다 — 배치 B가 아직
진행 중인 상태에서 두 테이블의 tenant 개념을 결합하면 배치 B 완료
순서에 이 테이블이 결합돼 버리므로, 결합은 리포지토리 어댑터 리프에서
애플리케이션 레벨로 검증한다.

`tenant_id`/`portfolio_id` 컬럼 구성은 FA-4(963d5f3cfb1b)가
`pos_account`/`pos_snapshot`에 `portfolio_id`를 추가한 방식과 동일하게
`portfolio_id`는 nullable로 둔다(부트스트랩 데이터가 사용자 전원에게
없음, 같은 이유). RLS는 PLT-30 M5(b3c7f19ad2e6)의 `tenant_isolation`
정책 형태를 그대로 쓴다 — 신설 테이블이라 레거시 테이블처럼 ENABLE을
보류할 이유가 없어 바로 ENABLE한다(CH-5 e1d9b5ed8d7d와 동일 판단).
"""

from collections.abc import Sequence

from alembic import op

from src.data.models.base import Currency

revision: str = "ddbd3a33bf30"
down_revision: str | Sequence[str] | None = "47ec4b178f54"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_TABLE = "pos_borrow_position"


def _sql_enum_members() -> str:
    return ", ".join(f"'{member.value}'" for member in Currency)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {_TABLE} (
            position_key    VARCHAR(200) PRIMARY KEY,
            tenant_id       UUID NOT NULL REFERENCES tenant(id),
            portfolio_id    UUID REFERENCES portfolio(portfolio_id),
            short_quantity  NUMERIC(30,10) NOT NULL CHECK (short_quantity > 0),
            supply_rate     NUMERIC(20,10) NOT NULL CHECK (supply_rate >= 0),
            currency        VARCHAR(10) NOT NULL
                CHECK (currency IN ({_sql_enum_members()})),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(f"CREATE INDEX idx_{_TABLE}_tenant_id ON {_TABLE}(tenant_id)")
    op.execute(
        f"CREATE INDEX idx_{_TABLE}_portfolio_id ON {_TABLE}(portfolio_id) "
        "WHERE portfolio_id IS NOT NULL"
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO {_APP_ROLE}")

    op.execute(
        f"CREATE POLICY tenant_isolation ON {_TABLE} "
        "USING (tenant_id::text = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))"
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.execute(f"DROP TABLE {_TABLE}")
