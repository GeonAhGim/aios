"""LB-8 — 포지션 저널 스키마(pos_account·pos_journal·pos_snapshot·pos_nav_daily).

Revision ID: 4a1d0c0de004
Revises: 4a1d0c0de006
Create Date: 2026-09-03 20:30:00.000000

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §3.2 (B)
(`CostMethod`, `JournalEntryType`, `PositionJournalEntryView`,
`PositionSnapshotView`, `NAVSnapshot`), §4.3, §9 LB-8.

리비전 id는 라벨일 뿐 alembic 체인 순서와 무관하다(PM 결정) —
`down_revision`은 이번 사이클 유일한 head인 LC-7(`4a1d0c0de006`)이다.

이 리프는 물리 스키마만 만든다 — 저널 append·스냅샷 fold(LB-2~LB-7)의
규칙은 여기 재구현하지 않는다(LC-6/LC-7 docstring과 동일한 드리프트 방지
원칙). `pos_account.cost_method`/`pos_snapshot.cost_method`는
`contracts/v1.py`의 `CostMethod`를, `pos_journal.entry_type`은
`JournalEntryType`을 그대로 재사용해 CHECK를 만든다.

`venue`는 아직 코드에 `Venue` enum이 없다(A `market_data` 모듈 미착수) —
기존 `orders`/`positions`/`exchange_credentials`의 `exchange VARCHAR(30)`과
같은 관행대로 CHECK 없는 자유 문자열로 둔다. `instrument_id`도 같은 이유로
FK를 걸지 않는다(참조할 `instrument_ref` 테이블이 아직 없음).

`pos_journal.digest`(idempotency 재전송 판별, §4.3 3행)는
`pos_journal.entry_hash`/`prev_hash`(해시체인, LC-6 `ledger_journal_entry`와
동일 목적)와 다른 값이다 — 전자는 커맨드 payload의 sha256, 후자는 저널
행 자체의 체인 해시.

`pos_snapshot.quantity`의 "CHECK(≥0 OR asset_class 파생)"(§9 표)는 DB
CHECK로 강제하지 않는다 — `asset_class`는 `pos_snapshot`의 컬럼이 아니라
(아직 없는) `instrument_ref`에만 있어 단일 테이블 CHECK로 표현할 수
없고, §4.3 표가 이미 이 불변을 코드(`snapshot_builder.apply_one`) 강제로
명시한다. `pos_account`/`pos_snapshot`은 WORM이 아니다(정상적으로
갱신되는 가변 상태 — LC-6 `ledger_balance`와 동일 이유). `pos_journal`·
`pos_nav_daily`만 WORM(L0-3 `append_only.py`)이다.

`aios_app`에 대한 GRANT를 이 리프에서 직접 실행하는 이유는 LC-6/LC-7과
동일하다(`4a1d0c0de001`의 `ALTER DEFAULT PRIVILEGES`가 `aios_migrator`
전용이라 이 저장소의 마이그레이션 실행 계정에는 적용되지 않음, R9
미해결).
"""
from collections.abc import Sequence
from enum import Enum

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql
from src.data.models.base import Currency
from src.foundation.positions.contracts.v1 import CostMethod, JournalEntryType

# revision identifiers, used by Alembic.
revision: str = "4a1d0c0de004"
down_revision: str | Sequence[str] | None = "4a1d0c0de006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_WORM_TABLES = ("pos_journal", "pos_nav_daily")


def _sql_enum_members(enum_cls: type[Enum]) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_cls)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE pos_account (
            account_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      UUID NOT NULL REFERENCES users(user_id),
            venue          VARCHAR(20) NOT NULL,
            connection_id  UUID REFERENCES account_connection(id),
            base_currency  VARCHAR(10) NOT NULL
                CHECK (base_currency IN ({_sql_enum_members(Currency)})),
            cost_method    VARCHAR(20) NOT NULL
                CHECK (cost_method IN ({_sql_enum_members(CostMethod)})),
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, venue, connection_id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE pos_journal (
            id                 BIGSERIAL PRIMARY KEY,
            tenant_id          UUID NOT NULL REFERENCES users(user_id),
            account_id         UUID NOT NULL REFERENCES pos_account(account_id),
            position_key       VARCHAR(200) NOT NULL,
            sequence_no        BIGINT NOT NULL CHECK (sequence_no >= 1),
            entry_type         VARCHAR(20) NOT NULL
                CHECK (entry_type IN ({_sql_enum_members(JournalEntryType)})),
            qty_delta          NUMERIC(30,10) NOT NULL,
            price              NUMERIC(30,10),
            price_ccy          VARCHAR(10)
                CHECK (price_ccy IN ({_sql_enum_members(Currency)})),
            fee                NUMERIC(30,10),
            fee_ccy            VARCHAR(10)
                CHECK (fee_ccy IN ({_sql_enum_members(Currency)})),
            realized_pnl_base  NUMERIC(30,10) NOT NULL DEFAULT 0,
            fx_rate            NUMERIC(20,10),
            fx_source          VARCHAR(50),
            source_event_type  VARCHAR(50) NOT NULL,
            source_event_id    VARCHAR(200) NOT NULL,
            idempotency_key    VARCHAR(250) NOT NULL UNIQUE,
            digest             VARCHAR(64) NOT NULL,
            prev_hash          VARCHAR(64),
            entry_hash         VARCHAR(64) NOT NULL,
            occurred_at        TIMESTAMPTZ NOT NULL,
            recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (position_key, sequence_no)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE pos_snapshot (
            position_key         VARCHAR(200) PRIMARY KEY,
            tenant_id            UUID NOT NULL REFERENCES users(user_id),
            account_id           UUID NOT NULL REFERENCES pos_account(account_id),
            instrument_id        UUID NOT NULL,
            quantity             NUMERIC(30,10) NOT NULL,
            avg_cost             NUMERIC(30,10) NOT NULL DEFAULT 0,
            cost_method          VARCHAR(20) NOT NULL
                CHECK (cost_method IN ({_sql_enum_members(CostMethod)})),
            lots                 JSONB NOT NULL DEFAULT '[]'::jsonb,
            realized_pnl_base    NUMERIC(30,10) NOT NULL DEFAULT 0,
            unrealized_pnl_base  NUMERIC(30,10),
            fees_base            NUMERIC(30,10) NOT NULL DEFAULT 0,
            funding_base         NUMERIC(30,10) NOT NULL DEFAULT 0,
            mark_price           NUMERIC(30,10),
            mark_at              TIMESTAMPTZ,
            last_journal_seq     BIGINT NOT NULL DEFAULT 0,
            legacy_position_id   BIGINT REFERENCES positions(id),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE pos_nav_daily (
            nav_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id        UUID NOT NULL REFERENCES pos_account(account_id),
            nav_date          DATE NOT NULL,
            base_currency     VARCHAR(10) NOT NULL
                CHECK (base_currency IN ({_sql_enum_members(Currency)})),
            opening_nav       NUMERIC(30,10) NOT NULL,
            cash              NUMERIC(30,10) NOT NULL,
            positions_mv      NUMERIC(30,10) NOT NULL,
            realized          NUMERIC(30,10) NOT NULL DEFAULT 0,
            unrealized_delta  NUMERIC(30,10) NOT NULL DEFAULT 0,
            funding           NUMERIC(30,10) NOT NULL DEFAULT 0,
            fees              NUMERIC(30,10) NOT NULL DEFAULT 0,
            flows             NUMERIC(30,10) NOT NULL DEFAULT 0,
            closing_nav       NUMERIC(30,10) NOT NULL,
            fx_rates          JSONB NOT NULL DEFAULT '[]'::jsonb,
            source_hash       VARCHAR(64) NOT NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (account_id, nav_date),
            CHECK (closing_nav = cash + positions_mv)
        )
        """
    )

    for table in ("pos_account", "pos_journal", "pos_snapshot", "pos_nav_daily"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON pos_journal_id_seq TO {_APP_ROLE}")

    for table in _WORM_TABLES:
        for statement in worm_sql(table):
            op.execute(statement)


def downgrade() -> None:
    for table in _WORM_TABLES:
        for statement in worm_drop_sql(table):
            op.execute(statement)

    op.execute("DROP TABLE pos_nav_daily")
    op.execute("DROP TABLE pos_snapshot")
    op.execute("DROP TABLE pos_journal")
    op.execute("DROP TABLE pos_account")
