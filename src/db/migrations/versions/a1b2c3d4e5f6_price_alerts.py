"""price_alerts — FD-14(신설) 가격/지표 알림

Revision ID: a1b2c3d4e5f6
Revises: 946d3f25d19a
Create Date: 2026-09-01 00:00:00.000000

Spec: 사용자 요청(2026-09-01) — "가격/지표 알림" 기능 신설. 조건 스키마는
condition_compiler.py/preview_service.py가 이미 쓰는 지표+연산자+임계값
형태를 그대로 재사용한다(params는 IndicatorService.calculate()가 받는
그 파라미터, 예: {"timeperiod": 14}). status='ACTIVE'인 행만 백그라운드
평가 루프(alert_service.py::evaluate_all_active)가 주기적으로 검사한다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "946d3f25d19a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE price_alerts (
            id              BIGSERIAL PRIMARY KEY,
            user_id         UUID NOT NULL REFERENCES users(user_id),
            exchange        VARCHAR(30) NOT NULL,
            symbol          VARCHAR(50) NOT NULL,
            timeframe       VARCHAR(10) NOT NULL DEFAULT '1h',
            indicator       VARCHAR(20) NOT NULL,
            params          JSONB NOT NULL DEFAULT '{}',
            operator        VARCHAR(20) NOT NULL,
            threshold       DOUBLE PRECISION NOT NULL,
            status          VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                CHECK (status IN ('ACTIVE', 'TRIGGERED', 'CANCELLED')),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            triggered_at    TIMESTAMPTZ,
            triggered_value DOUBLE PRECISION
        )
        """
    )
    op.execute("CREATE INDEX idx_price_alerts_user ON price_alerts(user_id)")
    op.execute(
        "CREATE INDEX idx_price_alerts_active ON price_alerts(status) WHERE status = 'ACTIVE'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE price_alerts")
