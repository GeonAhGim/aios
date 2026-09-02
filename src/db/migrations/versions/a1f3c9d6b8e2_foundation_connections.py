"""foundation_connections — FND-05 Connected Asset(읽기전용 계좌 연결)

Revision ID: a1f3c9d6b8e2
Revises: 4453afe74725
Create Date: 2026-09-02 00:00:00.000000

Spec: AIOSproject 44_connected_asset_readonly_connection_specification_v1.0.md,
74_connected_asset_l3_build_and_operational_specification_v1.0.md,
71_mihwa_aios_foundation_implementation_work_packages_v1.0.md FND-05.

스콥 축소(명시, FND-01/02와 같은 원칙):
- 74번 §3의 "vault capability"는 별도 서비스가 아니라 기존
  `src/core/security/encryption.py`(MFA/exchange credential이 이미 쓰는
  동일 AES-256-GCM 유틸)로 암호화한 opaque 참조를 `vault_secret_ref`에
  저장하는 것으로 대체한다 — 별도 vault 마이크로서비스는 92번/35번 §2.6이
  미래 컴포넌트로 명시한 대상이라 이 리프에서 만들지 않는다.
- 74번 §2의 실 OAuth/브라우저 handshake, 백그라운드 sync 스케줄러,
  주기적 HealthCheck 워커는 이 리프에 없다(71번 §6 "provider/legal review
  후 결정" — 실 provider 연동 자체가 이 리프 스콥 밖). `ConfirmConnection`
  커맨드가 CONNECTING/ACTIVE_READONLY 전이를 동기적으로 한 번에 수행하고,
  `SyncSnapshot` 커맨드는 수동 호출(향후 스케줄러가 같은 커맨드를 주기
  호출하도록 확장 가능 — 커맨드 계약 자체는 이미 그 형태).
- `connection_consent`는 connection당 최신 1건만 유지한다(PRIMARY KEY를
  connection_id로 둠) — Trust Core(FND-01)가 동의 이력 자체의 소유자이므로
  (71번 §4), 이 테이블은 "이 connection이 지금 근거로 삼는 동의가 무엇인가"
  포인터만 필요하고 이력 보존은 Trust 쪽 책임이다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f3c9d6b8e2"
down_revision: str | None = "4453afe74725"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE account_connection (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES users(user_id),
            owner_subject_id   UUID NOT NULL REFERENCES users(user_id),
            provider_code      VARCHAR(50) NOT NULL,
            opaque_account_ref VARCHAR(200) NOT NULL,
            state              VARCHAR(20) NOT NULL DEFAULT 'PENDING_CONSENT'
                CHECK (state IN ('PENDING_CONSENT','CONNECTING','ACTIVE_READONLY',
                                  'DEGRADED','REVOKED','DISCONNECTED')),
            capability_profile TEXT[] NOT NULL,
            revision           INT NOT NULL DEFAULT 1 CHECK (revision > 0),
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, provider_code, opaque_account_ref)
        )
        """
    )
    op.execute("CREATE INDEX ix_account_connection_tenant_id ON account_connection (tenant_id)")

    op.execute(
        """
        CREATE TABLE credential_binding (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            connection_id     UUID NOT NULL UNIQUE REFERENCES account_connection(id),
            vault_secret_ref  TEXT NOT NULL,
            scope_fingerprint VARCHAR(128) NOT NULL,
            credential_class  VARCHAR(20) NOT NULL DEFAULT 'READONLY'
                CHECK (credential_class = 'READONLY'),
            expires_at        TIMESTAMPTZ,
            rotation_state    VARCHAR(20) NOT NULL DEFAULT 'CURRENT'
        )
        """
    )

    op.execute(
        """
        CREATE TABLE connection_consent (
            connection_id UUID PRIMARY KEY REFERENCES account_connection(id),
            consent_ref   UUID NOT NULL,
            data_purposes TEXT[] NOT NULL,
            expires_at    TIMESTAMPTZ
        )
        """
    )

    op.execute(
        """
        CREATE TABLE account_snapshot (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            connection_id       UUID NOT NULL REFERENCES account_connection(id),
            captured_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            provider_as_of      TIMESTAMPTZ NOT NULL,
            freshness           VARCHAR(30) NOT NULL,
            currency            VARCHAR(10) NOT NULL,
            source_evidence_ref TEXT NOT NULL,
            UNIQUE (connection_id, provider_as_of, source_evidence_ref)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_account_snapshot_connection_id ON account_snapshot (connection_id)"
    )

    # 74번 §1 "one latest projection + append history" — append-only, id는
    # 이력 각 행의 PK일 뿐 최신값 선택은 evaluated_at 정렬로 한다.
    op.execute(
        """
        CREATE TABLE connection_health (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            connection_id      UUID NOT NULL REFERENCES account_connection(id),
            evaluated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            state              VARCHAR(20) NOT NULL CHECK (state IN ('HEALTHY','DEGRADED')),
            error_code         VARCHAR(50),
            retry_after        TIMESTAMPTZ,
            provider_trace_ref TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_connection_health_connection_id ON connection_health "
        "(connection_id, evaluated_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE connection_health")
    op.execute("DROP TABLE account_snapshot")
    op.execute("DROP TABLE connection_consent")
    op.execute("DROP TABLE credential_binding")
    op.execute("DROP TABLE account_connection")
