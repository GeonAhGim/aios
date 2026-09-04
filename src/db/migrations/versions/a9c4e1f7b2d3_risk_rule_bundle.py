"""risk_rule_bundle — R-22 partial unique·WORM 컬럼·conditional 전이.

Revision ID: a9c4e1f7b2d3
Revises: 9049e2b6b0b7
Create Date: 2026-09-04 00:00:00.000000

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.4/§9 R-22 (선행 R-15 9a1737a).

I6(scope당 ACTIVE 1개)은 partial unique `ux_bundle_active ON (scope) WHERE
state='ACTIVE'`가 DB에서 강제한다 — 두 트랜잭션이 서로 다른 번들을 동시에
같은 scope로 ACTIVE 전이시키면 나중에 커밋하는 쪽이 unique violation으로
거부된다(애플리케이션 락 불필요).

I7(`rule_hash`·`policy_snapshot`는 WORM)은 L0-3([[src/core/db/append_only.py]])
`worm_sql()`의 "REVOKE는 소유자를 못 막고 트리거는 막는다"는 원칙을 그대로
쓰되, 이 테이블은 `state`(DRAFT→APPROVED→ACTIVE→RETIRED)가 계속 UPDATE되어야
하므로 `worm_sql()`의 전체 행 append-only 트리거를 그대로 재사용할 수 없다
(모든 UPDATE를 막으면 상태 전이 자체가 불가능해진다). 대신 같은 "트리거는
소유자에게도 예외 없이 발동한다"는 원칙으로 `rule_hash`/`policy_snapshot`/
`version` 3개 컬럼만 골라 비교하는 `BEFORE UPDATE` 가드를 이 리비전에 직접
둔다 — 새 WORM *방식*이 아니라 같은 방식을 컬럼 단위로 좁혀 쓰는 것이다.
`version`도 같이 잠그는 이유: `UNIQUE(scope, version)`이 발행 이력의
정체성이라 사후 변경은 이력 위조와 같다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9c4e1f7b2d3"
down_revision: str | Sequence[str] | None = "9049e2b6b0b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE risk_rule_bundle (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            scope            VARCHAR(30) NOT NULL DEFAULT 'GLOBAL',
            version          VARCHAR(40) NOT NULL,
            rule_hash        CHAR(64) NOT NULL,
            engine_version   VARCHAR(40) NOT NULL,
            policy_snapshot  JSONB NOT NULL,
            state            VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
                CHECK (state IN ('DRAFT', 'APPROVED', 'ACTIVE', 'RETIRED')),
            effective_from   TIMESTAMPTZ,
            effective_to     TIMESTAMPTZ,
            created_by       UUID NOT NULL,
            approved_by      UUID,
            approval_ref     TEXT,
            approved_at      TIMESTAMPTZ,
            activated_at     TIMESTAMPTZ,
            retired_at       TIMESTAMPTZ,
            UNIQUE (scope, version),
            CHECK (approved_by IS NOT NULL OR state = 'DRAFT')
        )
        """
    )
    # I6 — scope당 ACTIVE 번들은 최대 1개. 이 인덱스 자체가 get_active(scope)의
    # 조회 경로이기도 하다.
    op.execute(
        "CREATE UNIQUE INDEX ux_bundle_active ON risk_rule_bundle (scope) "
        "WHERE state = 'ACTIVE'"
    )
    # I7 — rule_hash/policy_snapshot/version은 발행 후 불변(WORM). 모듈
    # docstring 참고: 전체 행이 아니라 이 3개 컬럼만 잠근다.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION risk_rule_bundle_worm_guard() RETURNS trigger AS $$
        BEGIN
            IF NEW.rule_hash IS DISTINCT FROM OLD.rule_hash
                OR NEW.policy_snapshot IS DISTINCT FROM OLD.policy_snapshot
                OR NEW.version IS DISTINCT FROM OLD.version THEN
                RAISE EXCEPTION
                    'WORM violation: rule_hash/policy_snapshot/version on '
                    'risk_rule_bundle is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER risk_rule_bundle_worm_guard_trg "
        "BEFORE UPDATE ON risk_rule_bundle "
        "FOR EACH ROW EXECUTE FUNCTION risk_rule_bundle_worm_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS risk_rule_bundle_worm_guard_trg ON risk_rule_bundle")
    op.execute("DROP FUNCTION IF EXISTS risk_rule_bundle_worm_guard()")
    op.execute("DROP TABLE IF EXISTS risk_rule_bundle")
