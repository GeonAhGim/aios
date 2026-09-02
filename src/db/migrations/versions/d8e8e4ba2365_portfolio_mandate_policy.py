"""portfolio_mandate_policy — FND-02 Portfolio Mandate/Policy

Revision ID: d8e8e4ba2365
Revises: 84b7d0faf14f
Create Date: 2026-09-02 00:00:00.000000

Spec: AIOSproject 45_portfolio_mandate_and_policy_specification_v1.0.md,
75_portfolio_mandate_l3_build_and_operational_specification_v1.0.md,
71_mihwa_aios_foundation_implementation_work_packages_v1.0.md FND-02.

스콥 축소(명시, FND-01과 같은 원칙): 75번 §1의 `approval_binding`(별도
승인자 요구)은 만들지 않는다 — FND-01 마이그레이션(84b7d0faf14f)과 같은
이유로, 지금 이 코드베이스는 tenant마다 구성원이 정확히 하나뿐이라
"다른 사람이 승인"할 대상이 없다. 대신 material change는 기존
`reauthenticate()`(src/api/deps.py, 비밀번호+MFA 재확인)과 새
cooling_off_started_at 시간 경과로 대체한다 — 플랫폼 레벨 주문승인의
`mandatory_wait_seconds`(approval_settings_service.py) 패턴과 동일 원칙을
mandate 레벨에 적용한 것이다.

75번 §3 예시 규칙 6개(MAX_TOTAL_EXPOSURE, MAX_SINGLE_INSTRUMENT,
MIN_CASH_BUFFER, ALLOWED_AUTONOMY, FORBIDDEN_ASSET, MAX_DAILY_LOSS)만
구현한다 — 45번 §1의 8개 section(Objective/Risk Budget/Liquidity/Universe/
Exposure/Leverage/Autonomy/Values·Tax) 전체를 지금 다 만들지 않는다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8e8e4ba2365"
down_revision: str | None = "84b7d0faf14f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE portfolio_mandate (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id         UUID NOT NULL REFERENCES users(user_id),
            subject_id        UUID NOT NULL REFERENCES users(user_id),
            active_revision_id UUID,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id)
        )
        """
    )
    # 75번 §1 "one active mandate per subject/portfolio scope" — P0 스콥(단일
    # 포트폴리오)에서는 tenant당 mandate 하나로 충분하다. 복수 포트폴리오는
    # 후속 리프.
    op.execute(
        """
        CREATE TABLE mandate_revision (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            mandate_id             UUID NOT NULL REFERENCES portfolio_mandate(id),
            revision_no            INT NOT NULL CHECK (revision_no > 0),
            state                  VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
                CHECK (state IN ('DRAFT','PROPOSED','ACTIVE','PAUSED','SUPERSEDED','CANCELLED')),
            max_total_exposure_pct NUMERIC(5,2) NOT NULL CHECK (max_total_exposure_pct > 0
                AND max_total_exposure_pct <= 100),
            max_single_instrument_pct NUMERIC(5,2) NOT NULL CHECK (max_single_instrument_pct > 0
                AND max_single_instrument_pct <= 100),
            min_cash_buffer_pct    NUMERIC(5,2) NOT NULL CHECK (min_cash_buffer_pct >= 0
                AND min_cash_buffer_pct < 100),
            max_daily_loss_pct     NUMERIC(5,2) NOT NULL CHECK (max_daily_loss_pct > 0
                AND max_daily_loss_pct <= 100),
            allowed_autonomy       VARCHAR(20) NOT NULL
                CHECK (allowed_autonomy IN ('OBSERVE','PAPER','LIMITED_LIVE')),
            forbidden_assets       TEXT[] NOT NULL DEFAULT '{}',
            revision_hash          VARCHAR(128) NOT NULL,
            cooling_off_started_at TIMESTAMPTZ,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            activated_at           TIMESTAMPTZ,
            UNIQUE (mandate_id, revision_no)
        )
        """
    )
    op.execute(
        "ALTER TABLE portfolio_mandate ADD CONSTRAINT fk_portfolio_mandate_active_revision "
        "FOREIGN KEY (active_revision_id) REFERENCES mandate_revision(id)"
    )
    op.execute(
        "CREATE INDEX ix_mandate_revision_mandate_id ON mandate_revision (mandate_id)"
    )
    op.execute(
        """
        CREATE TABLE policy_bundle (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            mandate_revision_id UUID NOT NULL REFERENCES mandate_revision(id),
            compiler_version    VARCHAR(20) NOT NULL,
            rule_hash           VARCHAR(128) NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (mandate_revision_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE policy_decision (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES users(user_id),
            bundle_id           UUID NOT NULL REFERENCES policy_bundle(id),
            command_type        VARCHAR(50) NOT NULL,
            command_fingerprint VARCHAR(128) NOT NULL,
            outcome             VARCHAR(30) NOT NULL
                CHECK (outcome IN ('ALLOW','DENY','REQUIRE_APPROVAL','REQUIRE_REASSESSMENT',
                                    'PAUSE_REQUIRED')),
            reason_codes        TEXT[] NOT NULL DEFAULT '{}',
            obligations         TEXT[] NOT NULL DEFAULT '{}',
            evaluated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at          TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_policy_decision_tenant_id ON policy_decision (tenant_id)")
    # 75번 §3 "Denied decisions are cached only for their exact input fingerprint" —
    # 같은 tenant+command_fingerprint 재평가를 빠르게 찾기 위한 인덱스.
    op.execute(
        "CREATE INDEX ix_policy_decision_fingerprint "
        "ON policy_decision (tenant_id, command_fingerprint)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE policy_decision")
    op.execute("DROP TABLE policy_bundle")
    op.execute(
        "ALTER TABLE portfolio_mandate DROP CONSTRAINT fk_portfolio_mandate_active_revision"
    )
    op.execute("DROP TABLE mandate_revision")
    op.execute("DROP TABLE portfolio_mandate")
