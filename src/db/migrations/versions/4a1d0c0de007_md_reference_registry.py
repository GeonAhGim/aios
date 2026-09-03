"""LA-10 — 시장데이터 참조 레지스트리
(md_instrument·md_symbol_alias·md_corporate_action·md_venue_calendar_day).

Revision ID: 4a1d0c0de007
Revises: 4a1d0c0de004
Create Date: 2026-09-03 01:00:00.000000

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §3.1 (A)
(`InstrumentRef`, `CorporateAction`, `CalendarDay`), §9.2 LA-10.

명세 §9.2 LA-10은 리비전 id `4a1d0c0de002`를 지정하지만 그 id는 이미
positions_journal(004)/ledger_core(005)/ledger_holds_payouts(006)이
점유해 순번이 지났다(PM 결정, task-418) — 이 리프는 007로 대체하고
현재 유일한 head인 004(`positions_journal`) 뒤에 이어붙인다.

`md_symbol_alias`의 기간 중복 배제(`EXCLUDE USING gist`)는 `venue`/
`alias_symbol` 동등 비교를 range 겹침과 함께 걸어야 해서 `btree_gist`
확장이 필요하다(같은 마이그레이션에서 `CREATE EXTENSION IF NOT
EXISTS`). downgrade는 이 확장을 DROP하지 않는다 — 다른 객체가 이미
의존할 수 있는 공유 확장을 리프 하나가 되돌리는 건 안전하지 않다.

`venue_symbol`(거래소측 원 심볼)은 `md_instrument`가 아니라
`md_symbol_alias`에 최초 별칭으로 심는다 — RENAME 시 과거 별칭은
`valid_to`로 닫고 새 별칭을 insert하는 게 A3 생애주기 규칙이라, 고정된
`canonical_symbol`과 시간에 따라 바뀌는 별칭을 한 테이블에 두면 그
불변이 깨진다.

이 네 테이블은 WORM이 아니다(LA-11 캔들과 달리 참조데이터는
RENAME/SUSPEND/DELIST 전이와 캘린더 재적재(upsert)로 정상 갱신된다).
"""
from collections.abc import Sequence
from enum import Enum

from alembic import op

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import SymbolStatus, Venue

# revision identifiers, used by Alembic.
revision: str = "4a1d0c0de007"
down_revision: str | Sequence[str] | None = "4a1d0c0de004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_TABLES = (
    "md_instrument",
    "md_symbol_alias",
    "md_corporate_action",
    "md_venue_calendar_day",
)


def _sql_enum_members(enum_cls: type[Enum]) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_cls)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.execute(
        f"""
        CREATE TABLE md_instrument (
            instrument_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            venue             VARCHAR(20) NOT NULL
                CHECK (venue IN ({_sql_enum_members(Venue)})),
            canonical_symbol  VARCHAR(50) NOT NULL,
            venue_symbol      VARCHAR(50) NOT NULL,
            asset_class       VARCHAR(20) NOT NULL
                CHECK (asset_class IN ({_sql_enum_members(AssetClass)})),
            base              VARCHAR(20),
            quote             VARCHAR(20),
            tick_size         NUMERIC(20,10) NOT NULL,
            lot_size          NUMERIC(20,10) NOT NULL,
            status            VARCHAR(20) NOT NULL
                CHECK (status IN ({_sql_enum_members(SymbolStatus)})),
            listed_at         TIMESTAMPTZ NOT NULL,
            delisted_at       TIMESTAMPTZ,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (venue, canonical_symbol, listed_at)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE md_symbol_alias (
            alias_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            instrument_id  UUID NOT NULL REFERENCES md_instrument(instrument_id),
            venue          VARCHAR(20) NOT NULL,
            alias_symbol   VARCHAR(50) NOT NULL,
            valid_from     TIMESTAMPTZ NOT NULL,
            valid_to       TIMESTAMPTZ,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (venue, alias_symbol, valid_from),
            EXCLUDE USING gist (
                venue WITH =,
                alias_symbol WITH =,
                tstzrange(valid_from, COALESCE(valid_to, 'infinity'::timestamptz)) WITH &&
            )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE md_corporate_action (
            action_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            instrument_id  UUID NOT NULL REFERENCES md_instrument(instrument_id),
            action_type    VARCHAR(20) NOT NULL
                CHECK (action_type IN ('SPLIT','REVERSE_SPLIT','CASH_DIVIDEND','MERGER')),
            ex_date        DATE NOT NULL,
            ratio          NUMERIC(20,10) NOT NULL CHECK (ratio > 0),
            cash_amount    NUMERIC(20,10),
            source_ref     VARCHAR(200) NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (instrument_id, action_type, ex_date)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE md_venue_calendar_day (
            id              BIGSERIAL PRIMARY KEY,
            venue           VARCHAR(20) NOT NULL,
            trade_date      DATE NOT NULL,
            is_trading_day  BOOLEAN NOT NULL,
            open_at         TIMESTAMPTZ,
            close_at        TIMESTAMPTZ,
            early_close     BOOLEAN NOT NULL DEFAULT FALSE,
            source          VARCHAR(50) NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (venue, trade_date),
            CHECK (is_trading_day = (open_at IS NOT NULL))
        )
        """
    )

    for table in _TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON md_venue_calendar_day_id_seq TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE md_venue_calendar_day")
    op.execute("DROP TABLE md_corporate_action")
    op.execute("DROP TABLE md_symbol_alias")
    op.execute("DROP TABLE md_instrument")
