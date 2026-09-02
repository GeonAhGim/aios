"""users_mfa_verified_at — FND-01 trust 73번 §6 규칙 2 step-up 갭 보강

Revision ID: cdd905e63ffe
Revises: a6636fcf92fc
Create Date: 2026-09-03 00:10:00.000000

Spec: AIOSproject 73_trust_core_l3_build_and_operational_specification_v1.0.md §6
규칙 2 "Sensitive commands require auth level MFA_VERIFIED issued within
configured step-up window."

전수감사(agent-platform-12, docs/FULL_AUDIT_2026-09-02.md §2-B) 발견 —
`foundation_deps.get_tenant_context()`가 `mfa_verified = user.mfa_enabled`를
그대로 옮겨써서, "계정에 MFA가 켜져 있다"와 "이 세션이 최근 실제로 TOTP를
통과했다"를 구분하지 못했다(계정 설정과 세션 사실을 혼동). 이 컬럼은 TOTP가
실제로 통과한 시각을 기록해 그 구분을 가능하게 한다 — `get_tenant_context()`가
`now - mfa_verified_at <= STEP_UP_WINDOW`로 신선도를 직접 계산한다
(foundation_deps.py 참조).

기존 계정은 전부 NULL로 시작한다(마이그레이션 이전 로그인은 이 시각을
남기지 않았으므로) — fail-closed: 다음 로그인 전까지는 mfa_enabled=True인
계정도 step-up 미검증으로 취급된다(auth_service.py 상단 "검증 불가를
통과로 취급하지 않는다"는 기존 fail-safe 원칙과 동일한 방향)."""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cdd905e63ffe"
down_revision: str | None = "a6636fcf92fc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN mfa_verified_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN mfa_verified_at")
