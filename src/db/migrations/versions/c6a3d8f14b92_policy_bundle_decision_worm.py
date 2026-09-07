"""policy_bundle / policy_decision — CM-4 WORM enforcement retrofit.

Revision ID: c6a3d8f14b92
Revises: a1f3c9d2e5b7
Create Date: 2026-09-07 00:00:00.000000

Spec: docs/specs/L4_compliance_and_regulatory_v1.0.md#9 CM-4 (선행 CM-3
82a1206d `domain/rule_bundle.py`/`domain/evaluator.py`).

§9 CM-4 결정: "기존 policy_bundle·policy_decision에 부족분만 추가(WORM 트리거
확인 포함)" — 신규 테이블·신규 컬럼 없이, `d8e8e4ba2365`가 두 테이블을 만들 때
빠뜨린 것 하나만 채운다: append-only 강제. 두 테이블 모두 생성 이후 어떤
컬럼도 합법적으로 UPDATE될 필요가 없다(`policy_bundle`은 `risk_rule_bundle`
[[a9c4e1f7b2d3]]과 달리 state machine이 없고, `policy_decision`은
`risk_decision`[[b8d5f2a1c3e4]]과 같은 순수 append-only 판정 로그다) — 그래서
컬럼 단위 가드(R-22 방식)가 아니라 L0-3 [[src/core/db/append_only.py]]
`worm_sql()`의 전체 행 append-only 트리거를 두 테이블 모두에 그대로
재사용한다(재구현 금지 원칙, CM-3 docstring과 동일).

두 테이블 모두 `d8e8e4ba2365`(이 리프의 down_revision보다 훨씬 앞선 리프)에서
`4a1d0c0de001`(역할 분리) 이전에 만들어졌으므로, `ensure_roles_sql()`의
"GRANT ... ON ALL TABLES IN SCHEMA public TO aios_app"가 이미 두 테이블을
포함해 적용됐다 — `b8d5f2a1c3e4`가 `risk_decision`(그 이후에 새로 만든 테이블)에
했던 것과 달리 이 리프는 별도 GRANT가 필요 없다.

`policy_bundle`에 전체 행 WORM을 걸면 기존
`postgres_policy_repository.insert_policy_bundle()`의 `ON CONFLICT ... DO
UPDATE SET mandate_revision_id = EXCLUDED.mandate_revision_id`(동시 삽입
경쟁에서 진 쪽이 기존 행을 그대로 돌려받기 위한 관용구)이 자기 자신에 대한
UPDATE 한 번을 실제로 실행해 트리거에 막힌다 — 그래서 이 리프와 함께
어댑터도 `DO NOTHING` + 실패 시 재조회로 바꿔 UPDATE를 아예 만들지 않게
고친다(같은 커밋, 같은 리프).
"""
from collections.abc import Sequence

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql

# revision identifiers, used by Alembic.
revision: str = "c6a3d8f14b92"
down_revision: str | Sequence[str] | None = "a1f3c9d2e5b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORM_TABLES = ("policy_bundle", "policy_decision")


def upgrade() -> None:
    for table in _WORM_TABLES:
        for statement in worm_sql(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(_WORM_TABLES):
        for statement in worm_drop_sql(table):
            op.execute(statement)
