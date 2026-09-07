"""merge dual heads: task-1766 l2 coverage + task-1987 fa0a batch b calendar source

Revision ID: bd931e3aa0d4
Revises: 7020473c9e74, 2e35eea547f2
Create Date: 2026-09-07 14:04:18.141515

두 워커가 동시에 같은 부모(6325757fd371, task-1768 캘린더 source 폭 확장)를
merge 대상으로 삼아 각자 병합 스텁을 만들면서 또 다른 dual head가 됐다
(7020473c9e74=task-1766 쪽 병합, 2e35eea547f2=task-1987 쪽 병합). 순수
병합 스텁(빈 upgrade/downgrade)으로 다시 단일 head로 되돌린다.
"""
from collections.abc import Sequence

revision: str = "bd931e3aa0d4"
down_revision: str | Sequence[str] | None = ("7020473c9e74", "2e35eea547f2")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
