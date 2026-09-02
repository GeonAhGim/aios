"""LC-6 — 머니 원장 핵심 스키마(account/journal_entry/posting_line/balance/control).

Revision ID: 4a1d0c0de005
Revises: 4a1d0c0de001
Create Date: 2026-09-03 12:00:00.000000

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §4.4, §9 LC-6.

복식부기 원장의 물리 스키마. 계약(LC-1, `contracts/v1.py`)의 enum을 그대로
재사용해 CHECK 제약을 생성한다 — 마이그레이션이 손으로 문자열을 다시
나열하면 계약이 바뀔 때 DB 제약만 뒤에 남는 드리프트가 생긴다.

분개 균형(Σ차변=Σ대변, 단일 통화, §4.4 표)은 `ledger_posting_line`에 붙는
**DEFERRABLE INITIALLY DEFERRED** constraint trigger로 강제한다 — 일반
`AFTER INSERT` 트리거는 같은 entry의 다른 행이 아직 커밋 전이라도 매 행마다
실행되지만, deferred 트리거는 PostgreSQL이 커밋 직전으로 실행을 미뤄줘
그 시점엔 entry의 모든 행이 이미 INSERT되어 있다 — 그래서 매 행에서
집계해도(트리거가 행마다 한 번씩 재확인) 커밋 시점 값은 항상 최종 상태와
같다. WORM(L0-3 `append_only.py`)은 `ledger_journal_entry`·
`ledger_posting_line`에만 건다 — `ledger_balance`는 정상적으로 UPDATE되는
파생 상태(CQRS 프로젝션), `ledger_control`은 운영자가 UPDATE하는 단일
행이라 append-only가 아니다.

`aios_app`에 대한 GRANT를 이 리프에서 직접 실행한다 — L0-5
(`4a1d0c0de001`)의 `ensure_roles_sql`이 만든 `ALTER DEFAULT PRIVILEGES`는
"FOR ROLE aios_migrator"로 한정되어 있고, 이 저장소의 마이그레이션은
`aios_migrator`가 아니라 `DATABASE_URL`의 소유자 계정으로 실행되므로(R9,
아직 미해결) 새로 만드는 테이블에는 기본 권한이 적용되지 않는다. WORM
REVOKE는 PUBLIC 대상이라 이 GRANT를 무력화하지 않는다 — 실제 강제는
트리거가 한다(owner도 예외 없이 걸림).

house 계정(`e7f8a9b0c1d2_wallet_ledger.py`가 만든
`PLATFORM_HOUSE_USER_ID = 00000000-0000-0000-0000-000000000001` 사용자)의
`AVAILABLE` 서브계정을 시드해 커미션 정산(§4.4 HOLD_CAPTURED)이 이 리프
직후부터 계정을 찾을 수 있게 한다. 계정코드 문자열은
`domain/chart_of_accounts.py`의 상수와 같은 값을 하드코딩한다(다른
마이그레이션 `e7f8a9b0c1d2`·`f8a9b0c1d2e3`와 동일 관행 — 마이그레이션은
실행 시점의 도메인 모듈이 아니라 그 시점의 SQL을 기록해야 하므로 import
대신 문자열로 고정한다). `tests/integration/test_db_schema.py`가 두 값의
일치를 확인한다.
"""
from collections.abc import Sequence
from enum import Enum

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql
from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import AccountType, LedgerEventType, Side

# revision identifiers, used by Alembic.
revision: str = "4a1d0c0de005"
down_revision: str | Sequence[str] | None = "4a1d0c0de001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_WORM_TABLES = ("ledger_journal_entry", "ledger_posting_line")

PLATFORM_HOUSE_USER_ID = "00000000-0000-0000-0000-000000000001"

# domain/chart_of_accounts.py의 PLATFORM_* 상수·account_type과 같은 값.
_SEED_ACCOUNTS: list[tuple[str, AccountType]] = [
    ("PLATFORM:CASH_CLEARING", AccountType.ASSET),
    ("PLATFORM:COMMISSION_REVENUE", AccountType.REVENUE),
    ("PLATFORM:REFUND_RESERVE", AccountType.EXPENSE),
    ("PLATFORM:PAYOUT_CLEARING", AccountType.CLEARING),
    (f"USER:{PLATFORM_HOUSE_USER_ID}:AVAILABLE", AccountType.LIABILITY),
]


def _sql_enum(enum_cls: type[Enum]) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_cls)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE ledger_account (
            account_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      UUID REFERENCES users(user_id),
            account_code   VARCHAR(120) NOT NULL UNIQUE,
            account_type   VARCHAR(20) NOT NULL
                CHECK (account_type IN ({_sql_enum(AccountType)})),
            currency       VARCHAR(10) NOT NULL CHECK (currency IN ({_sql_enum(Currency)})),
            allow_negative BOOLEAN NOT NULL DEFAULT FALSE,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE ledger_journal_entry (
            entry_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            sequence_no     BIGINT NOT NULL UNIQUE CHECK (sequence_no >= 1),
            event_type      VARCHAR(30) NOT NULL
                CHECK (event_type IN ({_sql_enum(LedgerEventType)})),
            event_ref       VARCHAR(200) NOT NULL,
            idempotency_key VARCHAR(250) NOT NULL UNIQUE,
            lines_digest    VARCHAR(64) NOT NULL,
            prev_hash       VARCHAR(64),
            entry_hash      VARCHAR(64) NOT NULL,
            audit_event_id  UUID NOT NULL REFERENCES foundation_audit_event(id),
            posted_by       UUID REFERENCES users(user_id),
            posted_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE ledger_posting_line (
            line_id    BIGSERIAL PRIMARY KEY,
            entry_id   UUID NOT NULL REFERENCES ledger_journal_entry(entry_id),
            line_no    INT NOT NULL,
            account_id UUID NOT NULL REFERENCES ledger_account(account_id),
            side       VARCHAR(10) NOT NULL CHECK (side IN ({_sql_enum(Side)})),
            amount     NUMERIC(20,2) NOT NULL CHECK (amount > 0),
            currency   VARCHAR(10) NOT NULL CHECK (currency IN ({_sql_enum(Currency)})),
            UNIQUE (entry_id, line_no)
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION ledger_entry_balanced() RETURNS trigger AS $$
        DECLARE
            currency_count INT;
            debit_total NUMERIC(20,2);
            credit_total NUMERIC(20,2);
        BEGIN
            SELECT COUNT(DISTINCT currency) INTO currency_count
                FROM ledger_posting_line WHERE entry_id = NEW.entry_id;
            IF currency_count > 1 THEN
                RAISE EXCEPTION
                    'ledger entry %: posting lines use more than one currency', NEW.entry_id;
            END IF;

            SELECT COALESCE(SUM(amount) FILTER (WHERE side = 'DEBIT'), 0),
                   COALESCE(SUM(amount) FILTER (WHERE side = 'CREDIT'), 0)
                INTO debit_total, credit_total
                FROM ledger_posting_line WHERE entry_id = NEW.entry_id;
            IF debit_total <> credit_total THEN
                RAISE EXCEPTION 'ledger entry %: unbalanced (debit=%, credit=%)',
                    NEW.entry_id, debit_total, credit_total;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER ledger_entry_balanced_trg
            AFTER INSERT ON ledger_posting_line
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION ledger_entry_balanced()
        """
    )
    op.execute(
        """
        CREATE TABLE ledger_balance (
            account_id     UUID PRIMARY KEY REFERENCES ledger_account(account_id),
            balance        NUMERIC(20,2) NOT NULL DEFAULT 0,
            held           NUMERIC(20,2) NOT NULL DEFAULT 0 CHECK (held >= 0),
            pending_payout NUMERIC(20,2) NOT NULL DEFAULT 0 CHECK (pending_payout >= 0),
            allow_negative BOOLEAN NOT NULL DEFAULT FALSE,
            last_entry_seq BIGINT NOT NULL DEFAULT 0,
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (allow_negative OR balance - held >= 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ledger_control (
            id            INT PRIMARY KEY CHECK (id = 1),
            write_frozen  BOOLEAN NOT NULL DEFAULT FALSE,
            frozen_reason TEXT,
            frozen_at     TIMESTAMPTZ,
            unfrozen_by   UUID REFERENCES users(user_id)
        )
        """
    )
    op.execute("INSERT INTO ledger_control (id, write_frozen) VALUES (1, FALSE)")

    for table in (
        "ledger_account",
        "ledger_journal_entry",
        "ledger_posting_line",
        "ledger_balance",
        "ledger_control",
    ):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ledger_posting_line_line_id_seq TO {_APP_ROLE}")

    for table in _WORM_TABLES:
        for statement in worm_sql(table):
            op.execute(statement)

    for account_code, acct_type in _SEED_ACCOUNTS:
        op.execute(
            "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
            f"VALUES ('{account_code}', '{acct_type.value}', '{Currency.KRW.value}', FALSE)"
        )
    seed_codes = ", ".join(f"'{code}'" for code, _ in _SEED_ACCOUNTS)
    op.execute(
        "INSERT INTO ledger_balance (account_id, allow_negative) "
        "SELECT account_id, allow_negative FROM ledger_account "
        f"WHERE account_code IN ({seed_codes})"
    )


def downgrade() -> None:
    for statement in worm_drop_sql("ledger_posting_line"):
        op.execute(statement)
    op.execute("DROP TABLE ledger_posting_line")
    op.execute("DROP FUNCTION IF EXISTS ledger_entry_balanced()")

    for statement in worm_drop_sql("ledger_journal_entry"):
        op.execute(statement)
    op.execute("DROP TABLE ledger_journal_entry")

    op.execute("DROP TABLE ledger_balance")
    op.execute("DROP TABLE ledger_control")
    op.execute("DROP TABLE ledger_account")
