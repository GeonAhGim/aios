"""users / user_approval_settings + 미뤄둔 user_id FK 전체 연결

Revision ID: a1f2b3c4d5e6
Revises: 189bdd25a017
Create Date: 2026-08-28 00:26:09.061936

Spec: 13_multi_tenancy_auth_v1.3.md#§13.2, 기능설계문서_v1.20.md#FD-11.1/11.3

작업트리 11.1 — tasks/strategies/orders/positions/reconciliation_events/
audit_log/notifications/notification_preferences/approval_requests 9개
테이블이 "11.1에서 ALTER TABLE로 추가"라고 각자 마이그레이션 상단에
남겨둔 user_id FK를 여기서 한 번에 연결한다.

risk_profile/risk_profile_assessed_at 컬럼과 risk_profile_history 테이블은
제외 — 10번 문서가 이를 별도 리프(3.11/3.12, "11.1 이후")로 명시적으로
분리해뒀다(FD-15.2 착수 시 추가).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f2b3c4d5e6"
down_revision: str | None = "189bdd25a017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_TARGETS = (
    ("tasks", "user_id", "fk_tasks_user"),
    ("strategies", "owner_user_id", "fk_strategies_owner_user"),
    ("orders", "user_id", "fk_orders_user"),
    ("positions", "user_id", "fk_positions_user"),
    ("reconciliation_events", "user_id", "fk_reconciliation_events_user"),
    ("audit_log", "user_id", "fk_audit_log_user"),
    ("notifications", "user_id", "fk_notifications_user"),
    ("notification_preferences", "user_id", "fk_notification_preferences_user"),
    ("approval_requests", "user_id", "fk_approval_requests_user"),
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE users (
            user_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email           VARCHAR(255) NOT NULL UNIQUE,
            password_hash   VARCHAR(255) NOT NULL,
            display_name    VARCHAR(100),
            mfa_enabled     BOOLEAN NOT NULL DEFAULT FALSE,
            mfa_secret      VARCHAR(255),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at   TIMESTAMPTZ,
            status          VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                CHECK (status IN ('ACTIVE','SUSPENDED','PENDING_DELETION','DELETED')),
            deletion_requested_at TIMESTAMPTZ,
            is_verifier         BOOLEAN NOT NULL DEFAULT FALSE,
            is_platform_admin   BOOLEAN NOT NULL DEFAULT FALSE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE user_approval_settings (
            user_id                  UUID PRIMARY KEY REFERENCES users(user_id),
            mode                     VARCHAR(20) NOT NULL DEFAULT 'SOLO'
                CHECK (mode IN ('SOLO','DUAL')),
            second_approver_contact  VARCHAR(255),
            mandatory_wait_seconds   INT NOT NULL DEFAULT 60 CHECK (mandatory_wait_seconds >= 60),
            updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    for table, column, constraint in _FK_TARGETS:
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
            f"FOREIGN KEY ({column}) REFERENCES users(user_id)"
        )


def downgrade() -> None:
    for table, _column, constraint in reversed(_FK_TARGETS):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}")

    op.execute("DROP TABLE user_approval_settings")
    op.execute("DROP TABLE users")
