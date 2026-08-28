"""idempotency_keys

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-08-28 00:26:09.061936

Spec: 15번 문서 §15.1(금전 관련 POST에 Idempotency-Key 적용 원칙)

편차: 이 원칙이 FD-13.3(구매)/FD-18.5b(결제확인) 등 여러 곳에서
"Idempotency-Key 헤더" 요구사항으로 반복 언급되지만, 그 키를 실제로
저장·대조할 테이블이 스펙 어디에도 없었다 — 앱 조립 단계에서 실제로
필요해져 지금 신설한다. 응답을 그대로 캐시해뒀다가 동일 키 재요청 시
새 부작용(예: 중복 구매) 없이 그대로 반환한다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6e7f8a9b0c1"
down_revision: str | None = "c5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE idempotency_keys (
            key             VARCHAR(255) PRIMARY KEY,
            status_code     INT NOT NULL,
            response_body   JSONB NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE idempotency_keys")
