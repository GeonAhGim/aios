"""DC-4 — instruments/venue_listings(EXCLUDE USING gist).

Revision ID: dbaf260f2917
Revises: d0ff9ff2ec9c
Create Date: 2026-09-04 08:29:52.223487

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§3.2(심볼 마스터 계약), §4.1(데이터 fail-closed 불변조건), §9.2 DC-4.

`contracts/v2/instruments.py`(DC-1)의 `Instrument`/`VenueListing` 필드와
1:1로 맞춘다 — coverage_spans·entitlements는 DC-8 소관이라 여기 넣지 않는다.

§4.1은 두 불변조건을 명시한다: (1) `instrument_id` 불변, (2)
`venue_listings` 기간 겹침 금지. (2)는 `EXCLUDE USING gist`로 강제한다
(같은 venue·venue_symbol에서 `[listed_at, delisted_at)` 구간이 겹치는
행을 DB가 거부 — 앱 레벨 검사가 아니다). `btree_gist` 확장이 없으면
스칼라 동등 비교(venue, venue_symbol)를 EXCLUDE에 섞어 쓸 수 없다.
(1)은 PK 자체로는 막히지 않는다(참조 행이 없으면 UPDATE가 성공한다) —
`forbid_instrument_id_update` 트리거로 instrument_id 컬럼의 UPDATE 자체를
거부한다. downgrade는 `btree_gist` 확장을 DROP하지 않는다(다른 객체가
이미 의존할 수 있는 공유 확장을 리프 하나가 되돌리는 건 안전하지 않다 —
4a1d0c0de007과 동일 정책).
"""
from collections.abc import Sequence
from enum import Enum

from alembic import op

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2.instruments import InstrumentLifecycle

# revision identifiers, used by Alembic.
revision: str = "dbaf260f2917"
down_revision: str | Sequence[str] | None = "d0ff9ff2ec9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"


def _sql_enum_members(enum_cls: type[Enum]) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_cls)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.execute(
        f"""
        CREATE TABLE instruments (
            instrument_id     VARCHAR(26) PRIMARY KEY,
            asset_class       VARCHAR(20) NOT NULL
                CHECK (asset_class IN ({_sql_enum_members(AssetClass)})),
            base              VARCHAR(20),
            quote             VARCHAR(20),
            isin              VARCHAR(20),
            figi              VARCHAR(20),
            tick_size         NUMERIC(30,10) NOT NULL,
            lot_size          NUMERIC(30,10) NOT NULL,
            calendar_id       VARCHAR(50) NOT NULL,
            lifecycle_state   VARCHAR(20) NOT NULL
                CHECK (lifecycle_state IN ({_sql_enum_members(InstrumentLifecycle)})),
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION forbid_instrument_id_update() RETURNS trigger AS $$
        BEGIN
            IF NEW.instrument_id <> OLD.instrument_id THEN
                RAISE EXCEPTION 'instrument_id is immutable (% -> %)',
                    OLD.instrument_id, NEW.instrument_id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_instruments_immutable_id
        BEFORE UPDATE ON instruments
        FOR EACH ROW EXECUTE FUNCTION forbid_instrument_id_update()
        """
    )

    op.execute(
        f"""
        CREATE TABLE venue_listings (
            listing_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            instrument_id  VARCHAR(26) NOT NULL REFERENCES instruments(instrument_id),
            venue          VARCHAR(20) NOT NULL
                CHECK (venue IN ({_sql_enum_members(Venue)})),
            venue_symbol   VARCHAR(50) NOT NULL,
            listed_at      TIMESTAMPTZ NOT NULL,
            delisted_at    TIMESTAMPTZ,
            is_primary     BOOLEAN NOT NULL DEFAULT FALSE,
            CHECK (delisted_at IS NULL OR delisted_at > listed_at),
            UNIQUE (venue, venue_symbol, listed_at),
            EXCLUDE USING gist (
                venue WITH =,
                venue_symbol WITH =,
                tstzrange(listed_at, COALESCE(delisted_at, 'infinity'::timestamptz)) WITH &&
            )
        )
        """
    )

    for table in ("instruments", "venue_listings"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE venue_listings")
    op.execute("DROP TRIGGER IF EXISTS trg_instruments_immutable_id ON instruments")
    op.execute("DROP FUNCTION IF EXISTS forbid_instrument_id_update()")
    op.execute("DROP TABLE instruments")
