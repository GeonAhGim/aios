"""task-1768 — `md_venue_calendar_day.source`를 URL 저장 가능한 길이로 확장.

Revision ID: 6325757fd371
Revises: 3819cf8a5373
Create Date: 2026-09-07 05:00:00.000000

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9.2 LA-10, §10 R4.
ADR-2026-09-06-H D3: 거래소 캘린더 `source: UNVERIFIED`를 1차 출처(거래소
공식 공지·데이터 URL)로 해소한다. 기존 `VARCHAR(50)`은 짧은 placeholder
("UNVERIFIED")만 염두에 둔 길이라 실제 출처 URL + 수집 시각을 함께 담기엔
부족하다(`yaml_calendar_source.load_calendar`가 그 둘을 한 문자열로 합쳐
이 컬럼에 싣는다 — 별도 컬럼 신설은 RD-21 스콥을 벗어난다는 PM decision).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6325757fd371"
down_revision: str | Sequence[str] | None = "3819cf8a5373"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE md_venue_calendar_day ALTER COLUMN source TYPE VARCHAR(500)")


def downgrade() -> None:
    op.execute("ALTER TABLE md_venue_calendar_day ALTER COLUMN source TYPE VARCHAR(50)")
