"""reviews

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.7.md (Reviews, FD-13.9), 14번 문서 §14.2. 작업트리
3.17 — 13.9 착수 전 선행 필요(10번 문서 각주에 따라 지금 적용).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE reviews (
            id                  BIGSERIAL PRIMARY KEY,
            listing_id          BIGINT NOT NULL REFERENCES strategy_listings(id),
            reviewer_user_id    UUID NOT NULL REFERENCES users(user_id),
            rating              SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
            comment             TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (listing_id, reviewer_user_id)
        )
        """
    )
    op.execute("CREATE INDEX idx_reviews_listing ON reviews(listing_id)")


def downgrade() -> None:
    op.execute("DROP TABLE reviews")
