"""FA-0c — ledger_account 키 문법 정정 A: 문자열 계층 인코딩 대신
(entity_id, fund_id, portfolio_id, account_type) 구조적 컬럼.

Revision ID: 18965d657219
Revises: e5a8c5d4f6b7
Create Date: 2026-09-09 00:00:00.000000

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-0c
(§9 표 112행). PM 2026-09-08 decision(task-1942) — 착수 시점 `alembic heads`가
단일(e5a8c5d4f6b7, task-2357 R-51)임을 확인하고 그 값을 down_revision으로 썼다.

문제: `ledger_account.account_code`(`4a1d0c0de005:82`)는
`"USER:{uuid}:{sub}" | "PLATFORM:{NAME}"` 문자열에 계층을 인코딩하고 그 문자열
자체가 UNIQUE다. 두 포트폴리오가 같은 `account_type`의 계정을 만들려 할 때
account_code가 portfolio를 구분하지 못하면 UniqueViolation으로 거부된다
(§9 DoD, `tests/integration/foundation/ledger/test_fa0c_account_scope.py`가
이 충돌을 실제로 재현한다).

이 리프의 범위: `ledger_account`에 `entity_id`/`fund_id`/`portfolio_id`
컬럼을 추가하고, 새 `UNIQUE(tenant_id, entity_id, fund_id, portfolio_id,
account_type)` 제약을 **추가**한다(기존 `UNIQUE(account_code)`는 유지 —
아래 참고). `account_code`는 표시용 파생값으로 강등한다(생성은 유지,
`domain/chart_of_accounts.py`의 `parse_account_code`/`account_type`/
`allows_negative`가 그 문자열을 다시 파싱하는 것에 더는 의존하지 않는다).

**기존 `UNIQUE(account_code)`를 왜 드롭하지 않는가**: `ensure_account`
(`application/purchase_flow.py`)와 그 재사용처(`chargeback.py`·`payouts.py`·
`post_corporate_action_cash.py`·`refund.py`·`topup.py`·
`allocate_fills.py`·`legacy_wallet_bridge.py`·`backfill.py`)가 전부
`INSERT ... ON CONFLICT (account_code) DO NOTHING`로 멱등성을 얻는다 —
Postgres는 ON CONFLICT의 추론 대상이 실제 unique/exclusion 제약과 일치해야
하므로, 이 제약을 드롭하면 그 호출부 전체가 다음 호출부터
`there is no unique or exclusion constraint matching the ON CONFLICT
specification`으로 깨진다. 이 리프는 그 호출부들을 건드리지 않으므로(전부
USER/PLATFORM 계정이며 entity_id/fund_id/portfolio_id는 이 리프 이후에도
NULL로 남는다 — FA-4/FA-8이 포트폴리오 스코프 원장 쓰기를 실제로 배선한다),
`UNIQUE(account_code)`를 그대로 두고 새 구조적 제약을 **병행** 추가하는
쪽이 기존 회귀를 깨지 않는 유일한 안전한 선택이다.

FK는 걸지 않는다: 이 DB에는 아직 `legal_entity`/`fund`/`portfolio` 행이
하나도 없다(FA-2가 테이블만 만들었고, FA-1 기본 계층의 실사용자 백필은
별도 리프다 — `789c138f13fe`·`963d5f3cfb1b`도 같은 이유로 "행이 이미
존재하는 경우에만" 조건부 백필한다). FK를 걸면 아래 백필(플랫폼/하우스
계정을 위한 결정론적 entity/fund/portfolio id)이 즉시 FK 위반으로 실패한다.

백필: 기존 5개 행(`account_code`) 전부를
`chart_of_accounts.default_scope()`로 역산한다 — 이 함수는 USER 계정은
자신의 user_id, PLATFORM 계정은 house 식별자(`PLATFORM_HOUSE_USER_ID`,
동일 UUID)를 앵커로 `entities/domain/defaults.py`의 결정론 규칙을 재사용한다.
역산 불가(= `account_code`가 알려진 문법과 안 맞음) 행이 한 건이라도 있으면
예외를 그대로 전파해 마이그레이션을 실패시킨다(조용한 기본값 금지).
"""
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from src.foundation.ledger.domain.chart_of_accounts import default_scope

# revision identifiers, used by Alembic.
revision: str = "18965d657219"
down_revision: str | Sequence[str] | None = "e5a8c5d4f6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCOPE_UNIQUE_CONSTRAINT = "ledger_account_scope_account_type_uq"


def upgrade() -> None:
    op.execute("ALTER TABLE ledger_account ADD COLUMN entity_id UUID")
    op.execute("ALTER TABLE ledger_account ADD COLUMN fund_id UUID")
    op.execute("ALTER TABLE ledger_account ADD COLUMN portfolio_id UUID")
    op.execute(
        "CREATE INDEX idx_ledger_account_portfolio_id ON ledger_account(portfolio_id) "
        "WHERE portfolio_id IS NOT NULL"
    )

    _backfill_scope()

    op.execute(
        f"ALTER TABLE ledger_account ADD CONSTRAINT {_SCOPE_UNIQUE_CONSTRAINT} "
        "UNIQUE (tenant_id, entity_id, fund_id, portfolio_id, account_type)"
    )


def _backfill_scope() -> None:
    bind = op.get_bind()
    rows = bind.execute(text("SELECT account_id, account_code FROM ledger_account")).fetchall()
    for account_id, account_code in rows:
        # Raises InvalidAccountCodeError (uncaught, on purpose) if account_code
        # does not match the known grammar — fail the migration, do not default.
        scope = default_scope(account_code)
        bind.execute(
            text(
                "UPDATE ledger_account "
                "SET entity_id = :entity_id, fund_id = :fund_id, portfolio_id = :portfolio_id "
                "WHERE account_id = :account_id"
            ),
            {
                "entity_id": scope.entity_id,
                "fund_id": scope.fund_id,
                "portfolio_id": scope.portfolio_id,
                "account_id": account_id,
            },
        )


def downgrade() -> None:
    op.execute(f"ALTER TABLE ledger_account DROP CONSTRAINT {_SCOPE_UNIQUE_CONSTRAINT}")
    op.execute("DROP INDEX IF EXISTS idx_ledger_account_portfolio_id")
    op.execute("ALTER TABLE ledger_account DROP COLUMN portfolio_id")
    op.execute("ALTER TABLE ledger_account DROP COLUMN fund_id")
    op.execute("ALTER TABLE ledger_account DROP COLUMN entity_id")
