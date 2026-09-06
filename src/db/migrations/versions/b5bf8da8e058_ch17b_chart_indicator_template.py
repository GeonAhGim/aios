"""ch17b_chart_indicator_template — CH-17b

Revision ID: b5bf8da8e058
Revises: 4102098cbd0f
Create Date: 2026-09-07 05:05:18.645584

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.2,
§9.11 CH-17. `chart_indicator_template`은 CH-5 `chart_layout`(e1d9b5ed8d7d)
과 동일한 tenant 최상위 테이블 패턴을 따른다 — 차이점 둘: (1) `tenant_id`가
`tenant(id)`를 FK한다(ADR-2026-09-06-G §2 D0 — `users`를 잘못 FK하는 사고
재발 방지, `chart_layout`에는 이 FK가 없어 여기서 새로 바로잡는다),
(2) `UNIQUE(tenant_id, name)`으로 같은 테넌트 내 이름 중복을 스키마
차원에서 막는다(애플리케이션 계층 중복 체크로 경합을 남기지 않는다,
105번 §2.2와 동일 원칙).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b5bf8da8e058"
down_revision: str | Sequence[str] | None = "4102098cbd0f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE chart_indicator_template (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL REFERENCES tenant(id),
            owner_subject_id UUID NOT NULL,
            name             VARCHAR(120) NOT NULL,
            template         JSONB NOT NULL,
            revision         INTEGER NOT NULL DEFAULT 0,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_chart_indicator_template_tenant_name UNIQUE (tenant_id, name)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_chart_indicator_template_tenant "
        "ON chart_indicator_template(tenant_id)"
    )

    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON chart_indicator_template TO {_APP_ROLE}"
    )

    # PLT-30 M5(b3c7f19ad2e6)·CH-5(e1d9b5ed8d7d)와 동일한 tenant_isolation 정책.
    op.execute(
        "CREATE POLICY tenant_isolation ON chart_indicator_template "
        "USING (tenant_id::text = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))"
    )
    op.execute("ALTER TABLE chart_indicator_template ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE chart_indicator_template DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON chart_indicator_template")
    op.execute("DROP TABLE chart_indicator_template")
