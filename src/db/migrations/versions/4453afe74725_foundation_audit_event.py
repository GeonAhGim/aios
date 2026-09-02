"""foundation_audit_event — FND-03 Audit/Evidence

Revision ID: 4453afe74725
Revises: d8e8e4ba2365
Create Date: 2026-09-02 00:00:00.000000

Spec: AIOSproject 49_audit_evidence_and_explainability_specification_v1.0.md,
79_audit_evidence_l3_build_and_operational_specification_v1.0.md,
71_mihwa_aios_foundation_implementation_work_packages_v1.0.md FND-03.

스콥 축소(명시): 이 리프는 79번 §1의 4개 레코드(audit_event/evidence_object/
evidence_edge/explanation_record) 중 `audit_event`만 만든다 —
evidence_object/evidence_edge/explanation_record는 지금 이 코드베이스에
아무 소비자도 없다(35번 §9.2 "쓰이지 않을 모듈 증식 금지"). 71번 §3 FND-03
자체가 요구하는 산출물도 "AuditEvent/EvidenceReference envelope"이지
evidence graph 전체가 아니다.

이미 존재하는 legacy `audit_log`(마이그레이션 9ec8a1ee28d7, WORM이지만 해시
체인은 없음)를 대체하지 않는다 — dispute_resolution/verification/wallet/
watchdog 등 6개 이상의 기존 서비스가 그 테이블에 직접 쓰고 있어 건드리면
파급이 크다(71번 §1 "기존 legacy 코드를 리팩터링 없이 감싼다"). 이 새 테이블은
Foundation(FND-01 이후) bounded context 전용이며, 79번이 요구하는 tenant별
해시 체인·checkpoint는 legacy 테이블에는 소급 적용하지 않는다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4453afe74725"
down_revision: str | None = "d8e8e4ba2365"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE foundation_audit_event (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID REFERENCES users(user_id),  -- NULL = system 이벤트(79번 §1)
            sequence_no        BIGINT NOT NULL,
            aggregate_type     VARCHAR(100) NOT NULL,
            aggregate_id       UUID NOT NULL,
            aggregate_revision INT,
            action             VARCHAR(100) NOT NULL,
            outcome            VARCHAR(20) NOT NULL
                CHECK (outcome IN ('SUCCESS', 'DENIED', 'ERROR')),
            actor_subject_id   UUID,
            trace_id           UUID NOT NULL,
            payload_hash       VARCHAR(64) NOT NULL,
            payload            JSONB NOT NULL,
            classification     VARCHAR(20) NOT NULL DEFAULT 'INTERNAL'
                CHECK (classification IN
                    ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED', 'SECRET_REFERENCE')),
            previous_hash      VARCHAR(64),
            event_hash         VARCHAR(64) NOT NULL,
            occurred_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # 79번 §1 "per-aggregate sequence unique" + tenant별 체인 연결 지점 —
    # tenant_id가 NULL인 system 이벤트는 여러 행이 동시에 NULL이라 이 제약이
    # 적용되지 않으므로(Postgres UNIQUE는 NULL을 서로 다르다고 취급) 별도
    # partial index로 system 체인도 동일하게 강제한다.
    op.execute(
        "CREATE UNIQUE INDEX uq_foundation_audit_event_tenant_seq "
        "ON foundation_audit_event (tenant_id, sequence_no) WHERE tenant_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_foundation_audit_event_system_seq "
        "ON foundation_audit_event (sequence_no) WHERE tenant_id IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_foundation_audit_event_aggregate "
        "ON foundation_audit_event (aggregate_type, aggregate_id)"
    )
    op.execute(
        "CREATE INDEX ix_foundation_audit_event_tenant_occurred "
        "ON foundation_audit_event (tenant_id, occurred_at DESC)"
    )
    # 79번 §1 "append-only" — legacy audit_log(9ec8a1ee28d7)와 동일한 WORM 강제.
    op.execute("REVOKE UPDATE, DELETE ON foundation_audit_event FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TABLE foundation_audit_event")
