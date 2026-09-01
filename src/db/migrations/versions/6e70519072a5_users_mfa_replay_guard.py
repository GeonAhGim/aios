"""users_mfa_replay_guard

Revision ID: 6e70519072a5
Revises: 7a6b8e4ef2f5
Create Date: 2026-09-01 00:00:00.000001

Spec: docs/RED_TEAM_FINDINGS.md #13 — TOTP 코드 재사용(replay) 방지.

pyotp.TOTP.verify()는 valid_window(기본 0) 안에서만 검증할 뿐, 이미 성공한
코드를 다시 쓰는 것을 막지 않는다 — 같은 30초 구간 안에서 유출된 코드가
반복 재사용될 수 있었다. 사용자별 마지막으로 성공한 TOTP 타임코드를
저장해두고, 이미 사용한 타임코드와 같으면 같은 구간 안이라도 거부한다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6e70519072a5"
down_revision: str | Sequence[str] | None = "7a6b8e4ef2f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN mfa_last_used_timecode BIGINT")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN mfa_last_used_timecode")
