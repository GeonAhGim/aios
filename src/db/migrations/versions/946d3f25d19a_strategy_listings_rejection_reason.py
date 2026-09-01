"""strategy_listings_rejection_reason

Revision ID: 946d3f25d19a
Revises: 6e70519072a5
Create Date: 2026-09-01 00:00:00.000002

Spec: docs/RED_TEAM_FINDINGS.md #16 — VerificationService.decide()의
REJECT 사유가 어디에도 저장되지 않아 응답이 나간 순간 사라졌다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "946d3f25d19a"
down_revision: str | Sequence[str] | None = "6e70519072a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE strategy_listings ADD COLUMN rejection_reason TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE strategy_listings DROP COLUMN rejection_reason")
