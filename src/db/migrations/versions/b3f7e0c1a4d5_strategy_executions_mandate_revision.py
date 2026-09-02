"""strategy_executions mandate_revision_id — legacy 실행을 Foundation mandate에 연결

Revision ID: b3f7e0c1a4d5
Revises: 4747bb11f733
Create Date: 2026-09-03 00:00:00.000000

Spec: PM 배정 ③(agent-platform-12) — 전수감사 §6. order_service.submit()과
execution_service.start()의 pre_submit_gate/pre_start_gate는 이미
mandate_revision_id를 OrderContext에 받아 mandates.evaluate_policy()로
정식 평가하는 2층 로직을 갖췄지만(src/services/order_service/
foundation_gate.py), 지금까지 이 컬럼 자체가 없어 두 호출부 모두 항상
None을 넘겨(2층 스킵, audit_log만 기록) 결과적으로 legacy 실행 전체가
1층(kill switch)만 적용받고 있었다.

nullable, 아무도 자동으로 채우지 않는다 — 기존 실행은 계속 NULL(마이그레이션
직후에도 동작 동일, 회귀 없음). UI/API가 실행 생성 시 mandate revision을
선택해 연결하는 경로는 이 리프의 스콥 밖(별도 작업) — 이 컬럼은 그 경로가
쓸 수 있는 자리만 만든다. FK는 mandate_revision.id를 가리키되 ON DELETE는
지정하지 않는다(mandate revision은 현재 삭제 경로가 없음 — 상태 전이만
존재, FND-02 참조).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f7e0c1a4d5"
down_revision: str | Sequence[str] | None = "4747bb11f733"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE strategy_executions "
        "ADD COLUMN mandate_revision_id UUID REFERENCES mandate_revision(id)"
    )
    op.execute(
        "CREATE INDEX ix_strategy_executions_mandate_revision_id "
        "ON strategy_executions (mandate_revision_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_strategy_executions_mandate_revision_id")
    op.execute("ALTER TABLE strategy_executions DROP COLUMN mandate_revision_id")
