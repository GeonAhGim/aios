"""orders.sent_at — L4-14b 스키마 갭 보정.

Revision ID: c1f4a9e7b3d6
Revises: d0a580db5ce8
Create Date: 2026-09-06 19:30:00.000000

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §4.2 전이표
(`VALIDATED|SENT|...|SUBMITTED|sent_at` 행) — `outbox_dispatcher._send_submit()`
(task-1538)이 VALIDATED→SUBMITTED(SENT) 전이에서 `patch={"sent_at": ...}`를
쓰는데, 073beca589d5(L4-06, task-1563)의 §2-C 79번 행(`_row_to_order` 신규
컬럼 목록)이 `sent_at`을 빠뜨려 컬럼이 존재하지 않았다(task-1567 note —
실DB 통합테스트가 `UndefinedColumnError`로 드러낸 결함). `outbox_dispatcher`
쪽 patch는 스펙 그대로이므로 컬럼을 스펙에 맞춰 추가한다.

NULL 허용, DEFAULT 없음 — 073beca589d5의 나머지 §3.3 신규 컬럼과 동일 관례
(`version`만 NOT NULL DEFAULT 0). WORM/RLS/커버오버 트리거는 손대지 않는다
(073beca589d5의 `oms_enforce_order_transition_trg`는 컬럼 목록이 아니라
`status`/`version`/`filled_quantity`만 검사하므로 이 추가로 영향받지 않는다).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1f4a9e7b3d6"
down_revision: str | Sequence[str] | None = "d0a580db5ce8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE orders ADD COLUMN sent_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP COLUMN sent_at")
