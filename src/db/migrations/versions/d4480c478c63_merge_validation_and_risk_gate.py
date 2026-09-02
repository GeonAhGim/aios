"""merge_validation_and_risk_gate

Revision ID: d4480c478c63
Revises: 3b244535b311, c7d4e1a9f052
Create Date: 2026-09-02 17:22:50.064792

FND-04(strategy_validation, 3b244535b311)와 FND-06(risk_gate, c7d4e1a9f052)가
서로 다른 세션에서 동시에 같은 부모(a1f3c9d6b8e2)로부터 독립적으로 진행되며
`alembic heads`가 두 개로 갈라졌다 — 스키마 충돌은 없다(서로 다른 테이블).
`alembic merge`로 만든 순수 병합 지점이라 upgrade/downgrade에 실행할 내용이
없다.
"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "d4480c478c63"
down_revision: str | Sequence[str] | None = ("3b244535b311", "c7d4e1a9f052")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
