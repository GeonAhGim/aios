"""foundation_paper_control — FND-07 Paper Execution & Control

Revision ID: e91a4c2b7d63
Revises: d4480c478c63
Create Date: 2026-09-02 00:00:00.000000

Spec: AIOSproject 47_paper_execution_and_control_center_specification_v1.0.md,
77_paper_execution_control_l3_build_and_operational_specification_v1.0.md,
71_mihwa_aios_foundation_implementation_work_packages_v1.0.md FND-07.

스콥 축소(명시, FND-01/02/05/06과 같은 원칙):
- 77번 §2의 REQUESTED->PREPARING->READY 3단계를 REQUESTED/READY/FAILED로
  줄인다 — REQUEST와 PREPARE 사이에 실제 비동기 대기가 없어(외부 provider
  승인 등) 한 커맨드로 합친다(domain/rules.py 참조). PREPARING 상태 자체를
  테이블 CHECK에 넣지 않는다.
- `package_ref`는 FK가 아니라 TEXT다 — FND-04(strategy_packages)가 아직
  PAPER_ELIGIBLE 패키지 lifecycle을 구현하지 않아(validation-run까지만
  존재) 참조 무결성을 강제할 대상 테이블이 없다. FND-04가 package
  lifecycle을 구현하면 이 컬럼에 FK를 추가하는 후속 마이그레이션이 필요.
- `deployment_health`(77번 §1, 별도 상태 이력 테이블)는 만들지 않는다 —
  connections(FND-05)가 HealthCheck를 sync_snapshot 결과에 접합한 것과
  같은 이유로, DEGRADED state 자체가 최신 health를 표현하고 이력은 아직
  소비자가 없다.
- 실제 tick 워크플로 스케줄러는 없다 — `paper_order_intent`는 수동/향후
  스케줄러 호출을 위한 테이블만 존재한다.
- 이 리프는 새 Foundation 전용 kill switch(FND-06 safety_control)와
  연동하지만, 기존 실행 엔진(`src/services/execution_loop/`,
  `src/services/order_service/`, FROZEN)은 전혀 건드리지 않는다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e91a4c2b7d63"
down_revision: str | None = "d4480c478c63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE paper_deployment (
            id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id                     UUID NOT NULL REFERENCES users(user_id),
            connection_id                 UUID REFERENCES account_connection(id),
            package_ref                   TEXT NOT NULL,
            mandate_revision_id           UUID NOT NULL REFERENCES mandate_revision(id),
            adapter_type                  VARCHAR(50) NOT NULL,
            credential_class              VARCHAR(20) NOT NULL DEFAULT 'PAPER'
                CHECK (credential_class = 'PAPER'),
            endpoint_classification       VARCHAR(50) NOT NULL,
            provider_sandbox_account_ref  TEXT NOT NULL,
            state                         VARCHAR(20) NOT NULL DEFAULT 'REQUESTED'
                CHECK (state IN ('REQUESTED','READY','RUNNING','PAUSED','STOPPED','FAILED',
                                   'DEGRADED','RECOVERY_REVIEW')),
            fence_token                   BIGINT NOT NULL DEFAULT 0,
            created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                    TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_paper_deployment_tenant_id ON paper_deployment (tenant_id)")

    op.execute(
        """
        CREATE TABLE deployment_command (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            deployment_id     UUID NOT NULL REFERENCES paper_deployment(id),
            idempotency_key   VARCHAR(200) NOT NULL,
            command_type      VARCHAR(20) NOT NULL
                CHECK (command_type IN ('REQUEST','START','PAUSE','RESUME','STOP')),
            actor_subject_id  UUID NOT NULL REFERENCES users(user_id),
            outcome           VARCHAR(20) NOT NULL CHECK (outcome IN ('ACCEPTED','DENIED')),
            detail            TEXT,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (deployment_id, idempotency_key)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE paper_order_intent (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            deployment_id           UUID NOT NULL REFERENCES paper_deployment(id),
            sequence                INT NOT NULL,
            fence_token_at_submit   BIGINT NOT NULL,
            state                   VARCHAR(20) NOT NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (deployment_id, sequence)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE paper_order_intent")
    op.execute("DROP TABLE deployment_command")
    op.execute("DROP TABLE paper_deployment")
