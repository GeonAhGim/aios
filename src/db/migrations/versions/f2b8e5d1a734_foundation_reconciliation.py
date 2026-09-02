"""foundation_reconciliation — FND-08 Reconciliation & Resilience

Revision ID: f2b8e5d1a734
Revises: e91a4c2b7d63
Create Date: 2026-09-02 00:00:00.000000

Spec: AIOSproject 50_reconciliation_resilience_specification_v1.0.md,
80_reconciliation_resilience_l3_build_and_operational_specification_v1.0.md,
71_mihwa_aios_foundation_implementation_work_packages_v1.0.md FND-08.

스콥 축소(명시, FND-01/02/05/06/07과 같은 원칙):
- "internal snapshot"과 "provider snapshot" 둘 다 호출자가 타입 있는
  값으로 공급한다(EntitySnapshot, contracts/v1.py) — 이 코드베이스에
  paper_control(FND-07)이 아직 fill/position/balance를 실제로 기록하는
  내부 원장(ledger)이 없고(tick 워크플로 자체가 없음), connections(FND-05)의
  `account_snapshot`도 실제 잔고 숫자가 아니라 freshness 메타데이터만
  갖고 있다(74번 §1 스콥 축소 — 마이그레이션 a1f3c9d6b8e2 참조). 두
  내부 원장이 실제 값을 갖게 되면 이 커맨드의 입력 조립부만 교체하면 된다.
- `recovery_case`(80번 §1, owner/필요 승인/이력 별도 테이블)를 만들지
  않는다 — `reconciliation_state`에 `resolved_by`/`resolution_reason`/
  `resolved_at` 컬럼으로 접는다. "resolve alone cannot resume"(REC-007)은
  별도 승인 워크플로가 아니라 구조로 강제한다 — resolve 커맨드는
  safety_control(FND-06)을 절대 건드리지 않고, 재개는 여전히
  paper_control(FND-07)의 resume_deployment()가 별도로 요구하는 완전한
  재평가를 거쳐야 한다.
- 실제 스케줄러/lease 기반 워커 조정은 없다 — REC-004(동시 실행 dedupe)는
  `UNIQUE(target_ref, input_hash)` 제약 하나로 구현한다(같은 입력이면
  같은 해시 → 같은 행, 두 번째 삽입 시도는 기존 행을 반환).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2b8e5d1a734"
down_revision: str | None = "e91a4c2b7d63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CLASSIFICATIONS = (
    "HEALTHY",
    "PENDING",
    "MINOR_DIFFERENCE",
    "MATERIAL_MISMATCH",
    "PROVIDER_UNAVAILABLE",
    "INVESTIGATING",
    "RESOLVED",
)


def upgrade() -> None:
    classification_check = "'" + "','".join(_CLASSIFICATIONS) + "'"

    op.execute(
        """
        CREATE TABLE reconciliation_run (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      UUID NOT NULL REFERENCES users(user_id),
            target_type    VARCHAR(50) NOT NULL,
            target_ref     UUID NOT NULL,
            connection_id  UUID REFERENCES account_connection(id),
            input_hash     VARCHAR(128) NOT NULL,
            state          VARCHAR(20) NOT NULL CHECK (state IN ('COMPLETED','DEDUPED')),
            rule_version   VARCHAR(20) NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (target_ref, input_hash)
        )
        """
    )
    op.execute("CREATE INDEX ix_reconciliation_run_tenant_id ON reconciliation_run (tenant_id)")

    op.execute(
        f"""
        CREATE TABLE reconciliation_item (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id           UUID NOT NULL REFERENCES reconciliation_run(id),
            entity_type      VARCHAR(50) NOT NULL,
            entity_key       VARCHAR(100) NOT NULL,
            internal_value   NUMERIC(30,10) NOT NULL,
            provider_value   NUMERIC(30,10),
            classification   VARCHAR(20) NOT NULL
                CHECK (classification IN ({classification_check})),
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_reconciliation_item_run_id ON reconciliation_item (run_id)")

    op.execute(
        f"""
        CREATE TABLE reconciliation_state (
            target_ref         UUID PRIMARY KEY,
            target_type        VARCHAR(50) NOT NULL,
            tenant_id          UUID NOT NULL REFERENCES users(user_id),
            aggregate_status   VARCHAR(20) NOT NULL
                CHECK (aggregate_status IN ({classification_check})),
            last_healthy_at    TIMESTAMPTZ,
            last_checked_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            blocking_reason    TEXT,
            revision           INT NOT NULL DEFAULT 0,
            safety_control_id  UUID,
            resolved_by        UUID REFERENCES users(user_id),
            resolution_reason  TEXT,
            resolved_at        TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_reconciliation_state_tenant_id ON reconciliation_state (tenant_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE reconciliation_state")
    op.execute("DROP TABLE reconciliation_item")
    op.execute("DROP TABLE reconciliation_run")
