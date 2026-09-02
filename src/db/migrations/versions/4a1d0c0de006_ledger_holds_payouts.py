"""LC-7 — 홀드·정산배치·정산항목·무결성검사 스키마.

Revision ID: 4a1d0c0de006
Revises: 4a1d0c0de005
Create Date: 2026-09-03 18:00:00.000000

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §4.5, §3.3
(`HoldView`, `PayoutBatchView`, `IntegrityReport`), §9 LC-7.

이 리프는 물리 스키마만 만든다 — 홀드 상태전이(LC-5 `domain/hold_state.py`)·
정산 스케줄 산출(`domain/payout_schedule.py`)의 규칙은 여기 재구현하지
않는다(드리프트 방지, LC-6 docstring과 동일 원칙).

`ledger_hold.state`/`ledger_payout_batch.state`는 계약(`contracts/v1.py`)의
`HoldState` enum과 `PayoutBatchView.state` 리터럴을 그대로 재사용해 CHECK를
만든다. `PayoutBatchView.state`는 enum이 아니라 `Literal`이라 `_sql_enum`을
쓸 수 없어 문자열 튜플로 고정한다 — 계약이 바뀌면 이 튜플도 같이 바뀌어야
하고, `tests/integration/test_db_schema.py`가 그 일치를 확인한다.

`ledger_integrity_check`만 WORM(L0-3 `append_only.py`)이다: 무결성 검사
결과는 사후 변조되면 무결성 검사 자체가 무의미해지므로 append-only여야
한다. `ledger_hold`/`ledger_payout_batch`/`ledger_payout_item`은 상태가
정상적으로 바뀌는 가변 테이블(LC-6 `ledger_balance`와 동일 이유)이라
WORM을 걸지 않는다.

`aios_app`에 대한 GRANT를 이 리프에서 직접 실행하는 이유는 LC-6과 동일하다
(`4a1d0c0de001`의 `ALTER DEFAULT PRIVILEGES`가 `aios_migrator` 전용이라
이 저장소의 마이그레이션 실행 계정에는 적용되지 않음, R9 미해결).
"""
from collections.abc import Sequence
from enum import Enum

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql
from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import HoldState

# revision identifiers, used by Alembic.
revision: str = "4a1d0c0de006"
down_revision: str | Sequence[str] | None = "4a1d0c0de005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_WORM_TABLES = ("ledger_integrity_check",)

# PayoutBatchView.state(계약)와 같은 값 — 계약이 Literal이라 enum에서 못 뽑는다.
_PAYOUT_BATCH_STATES = ("SCHEDULED", "RELEASED", "PAID", "FAILED")
_INTEGRITY_RESULTS = ("OK", "DRIFT")


def _sql_enum(values: Sequence[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _sql_enum_members(enum_cls: type[Enum]) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_cls)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE ledger_hold (
            hold_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id       UUID NOT NULL REFERENCES ledger_account(account_id),
            amount           NUMERIC(20,2) NOT NULL CHECK (amount > 0),
            currency         VARCHAR(10) NOT NULL
                CHECK (currency IN ({_sql_enum_members(Currency)})),
            purpose          VARCHAR(120) NOT NULL,
            reference        VARCHAR(200) NOT NULL,
            state            VARCHAR(20) NOT NULL
                CHECK (state IN ({_sql_enum_members(HoldState)})),
            expires_at       TIMESTAMPTZ NOT NULL,
            entry_id         UUID NOT NULL REFERENCES ledger_journal_entry(entry_id),
            settled_entry_id UUID REFERENCES ledger_journal_entry(entry_id),
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (purpose, reference)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE ledger_payout_batch (
            batch_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            seller_user_id   UUID NOT NULL REFERENCES users(user_id),
            period_start     DATE NOT NULL,
            period_end       DATE NOT NULL,
            amount           NUMERIC(20,2) NOT NULL CHECK (amount > 0),
            currency         VARCHAR(10) NOT NULL
                CHECK (currency IN ({_sql_enum_members(Currency)})),
            state            VARCHAR(20) NOT NULL
                CHECK (state IN ({_sql_enum(_PAYOUT_BATCH_STATES)})),
            release_entry_id UUID REFERENCES ledger_journal_entry(entry_id),
            paid_entry_id    UUID REFERENCES ledger_journal_entry(entry_id),
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (seller_user_id, period_end),
            CHECK (period_start < period_end)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ledger_payout_item (
            item_id          BIGSERIAL PRIMARY KEY,
            batch_id         UUID NOT NULL REFERENCES ledger_payout_batch(batch_id),
            capture_entry_id UUID NOT NULL UNIQUE REFERENCES ledger_journal_entry(entry_id),
            amount           NUMERIC(20,2) NOT NULL CHECK (amount > 0)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE ledger_integrity_check (
            check_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            result     VARCHAR(10) NOT NULL CHECK (result IN ({_sql_enum(_INTEGRITY_RESULTS)})),
            report     JSONB NOT NULL
        )
        """
    )

    for table in (
        "ledger_hold",
        "ledger_payout_batch",
        "ledger_payout_item",
        "ledger_integrity_check",
    ):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ledger_payout_item_item_id_seq TO {_APP_ROLE}")

    for table in _WORM_TABLES:
        for statement in worm_sql(table):
            op.execute(statement)


def downgrade() -> None:
    for statement in worm_drop_sql("ledger_integrity_check"):
        op.execute(statement)
    op.execute("DROP TABLE ledger_integrity_check")

    op.execute("DROP TABLE ledger_payout_item")
    op.execute("DROP TABLE ledger_payout_batch")
    op.execute("DROP TABLE ledger_hold")
