"""ch5_chart_layouts_drawings — CH-5

Revision ID: e1d9b5ed8d7d
Revises: a7c3d9e1f2b4
Create Date: 2026-09-06 00:00:00.000000

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.2,
§9.6 CH-5. `chart_drawing_set`은 `chart_layout`의 자식 테이블(FK
`ON DELETE CASCADE`)이라 RLS 정책(PLT-30 M5, b3c7f19ad2e6)의 "부모 FK로
이미 간접 격리되는 자식 테이블은 제외" 원칙을 그대로 따라 여기서 RLS를
ENABLE하지 않는다 — `chart_layout`만 M5와 동일한 `tenant_isolation` 정책을
추가한다. `chart_layout` 생성 시 `chart_drawing_set` 빈 행(revision=0)을
같은 트랜잭션으로 항상 함께 만든다(adapters/postgres_repository.py) —
그래서 `chart_drawing_set`에는 layout_id 없는 행이 존재하지 않는다.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e1d9b5ed8d7d"
down_revision: str | Sequence[str] | None = "a7c3d9e1f2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE chart_layout (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL,
            owner_subject_id UUID NOT NULL,
            name             VARCHAR(120) NOT NULL,
            layout_state     JSONB NOT NULL,
            revision         INTEGER NOT NULL DEFAULT 0,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_chart_layout_tenant ON chart_layout(tenant_id)")

    op.execute(
        """
        CREATE TABLE chart_drawing_set (
            layout_id      UUID PRIMARY KEY REFERENCES chart_layout(id) ON DELETE CASCADE,
            schema_version INTEGER NOT NULL,
            drawings       JSONB NOT NULL,
            revision       INTEGER NOT NULL DEFAULT 0,
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    for table in ("chart_layout", "chart_drawing_set"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_APP_ROLE}")

    # PLT-30 M5(b3c7f19ad2e6)와 동일한 정책 형태 — foundation 최상위 컨텍스트
    # 테이블 한 개씩에 tenant_isolation을 건다는 원칙을 CH-5도 따른다.
    op.execute(
        "CREATE POLICY tenant_isolation ON chart_layout "
        "USING (tenant_id::text = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))"
    )
    op.execute("ALTER TABLE chart_layout ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE chart_layout DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON chart_layout")
    op.execute("DROP TABLE chart_drawing_set")
    op.execute("DROP TABLE chart_layout")
