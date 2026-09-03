"""LA-11 — 캔들·틱·인제스트 배치·품질이슈
(md_candle·md_quarantine_candle·md_tick·md_ingest_batch·md_quality_issue).

Revision ID: 4a1d0c0de008
Revises: 4a1d0c0de007
Create Date: 2026-09-03 02:00:00.000000

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §3.1 (A)
(`CandleRecord`, `TickRecord`, `QualityIssue`), §4.1 품질 게이트 판정표,
§9.2 LA-11.

명세 §9.2 LA-11은 리비전 id `4a1d0c0de003`을 지정하지만 그 id는
positions_journal(004)/ledger_core(005)/ledger_holds_payouts(006)/
md_reference_registry(007)이 이미 점유해 순번이 지났다(PM 결정,
task-450) — 이 리프는 008로 대체하고 현재 유일한 head인 007
(md_reference_registry, LA-10) 뒤에 이어붙인다.

CHECK 6종(§4.1 "OHLC 불일치" 행 — 코드 검증을 우회한 직접 INSERT도
막는 최후 방어선)은 `low<=min(open,close)`·`high>=max(open,close)`를
개별 부등식 6개로 전개한 것이다: high>=open, high>=close, high>=low,
low<=open, low<=close, volume>=0. `md_quarantine_candle`은 정의상 이
CHECK를 위반한 캔들을 담는 테이블이라 동일 CHECK를 걸지 않는다(걸면
격리 자체가 불가능해진다) — venue/timeframe/issue_type enum CHECK만
공유한다.

파티셔닝: `md_candle`·`md_tick`은 PostgreSQL 선언적 RANGE 파티션(월
단위, 파티션 키는 각각 open_time/traded_at)이다. 선언적 파티션의
UNIQUE/PK는 파티션 키 컬럼을 반드시 포함해야 한다는 PostgreSQL 제약
때문에 `md_tick`의 UNIQUE는 명세가 적은 `(venue, instrument_id,
trade_id)`가 아니라 `traded_at`을 더한 4컬럼이다(명세와의 편차,
task-450 note) — trade_id는 거래소가 파티션 경계를 넘어 재사용하지
않는다고 가정한다(미검증, 거래소 문서 확인 필요).

`md_ensure_partitions(months_ahead int)`는 SECURITY DEFINER로 만든다
— 런타임 `aios_app`은 스키마에 USAGE만 있고 CREATE 권한이 없어서(L0-5
`ensure_roles_sql`) invoker 권한으로는 파티션 테이블을 만들 수 없다.
정의자(마이그레이션 실행 계정, 테이블 소유자) 권한으로 실행해
스케줄러가 `aios_app`으로 접속한 채 주기적으로 이 함수만 호출해
미래 파티션을 채워 넣을 수 있게 한다. `BEFORE UPDATE OR DELETE` WORM
트리거는 파티션 부모에 걸면 PostgreSQL이 기존 파티션은 물론 이후
`md_ensure_partitions`가 만드는 파티션에도 자동으로 복제한다(PG11+
문서화된 동작) — 파티션마다 별도로 걸 필요가 없다.

WORM: `md_candle`·`md_ingest_batch`·`md_quality_issue`만(명세 표 그대로).
`md_quarantine_candle`·`md_tick`은 WORM이 아니다 — 격리 테이블은 운영자
정정 절차(§10 후속)가, 틱은 명세가 WORM 대상으로 나열하지 않는다.

`md_ingest_batch.audit_event_id`는 NOT NULL로 강제한다 — §4.1 배치
판정(ACCEPT부터 REJECT까지)은 전부 감사 이벤트를 낸다는 fail-closed
원칙(LC-6 `ledger_journal_entry.audit_event_id`와 동일 근거)이다.
"""
from collections.abc import Sequence
from enum import Enum

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql
from src.foundation.market_data.contracts.v1 import (
    QualityIssueType,
    Severity,
    Timeframe,
    Venue,
    Verdict,
)

# revision identifiers, used by Alembic.
revision: str = "4a1d0c0de008"
down_revision: str | Sequence[str] | None = "4a1d0c0de007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_WORM_TABLES = ("md_candle", "md_ingest_batch", "md_quality_issue")
_ALL_TABLES = (
    "md_candle",
    "md_quarantine_candle",
    "md_tick",
    "md_ingest_batch",
    "md_quality_issue",
)

_ENSURE_PARTITIONS_SQL = """
CREATE OR REPLACE FUNCTION md_ensure_partitions(months_ahead INT)
RETURNS void AS $$
DECLARE
    i INT;
    part_start TIMESTAMPTZ;
    part_end TIMESTAMPTZ;
    suffix TEXT;
BEGIN
    FOR i IN 0..months_ahead LOOP
        part_start := date_trunc('month', now()) + (i || ' months')::interval;
        part_end := part_start + interval '1 month';
        suffix := to_char(part_start, 'YYYY_MM');
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS md_candle_%s PARTITION OF md_candle '
            'FOR VALUES FROM (%L) TO (%L)',
            suffix, part_start, part_end
        );
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS md_tick_%s PARTITION OF md_tick '
            'FOR VALUES FROM (%L) TO (%L)',
            suffix, part_start, part_end
        );
    END LOOP;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
"""


def _sql_enum_members(enum_cls: type[Enum]) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_cls)


def upgrade() -> None:
    # md_ingest_batch가 md_candle.batch_id의 FK 대상이라 먼저 만든다.
    op.execute(
        f"""
        CREATE TABLE md_ingest_batch (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID REFERENCES users(user_id),
            source               VARCHAR(50) NOT NULL,
            venue                VARCHAR(20) NOT NULL
                CHECK (venue IN ({_sql_enum_members(Venue)})),
            instrument_id        UUID NOT NULL REFERENCES md_instrument(instrument_id),
            timeframe            VARCHAR(10) NOT NULL
                CHECK (timeframe IN ({_sql_enum_members(Timeframe)})),
            range_start          TIMESTAMPTZ NOT NULL,
            range_end            TIMESTAMPTZ NOT NULL,
            request_fingerprint  VARCHAR(128) NOT NULL,
            batch_hash           VARCHAR(64) NOT NULL,
            record_count         INTEGER NOT NULL DEFAULT 0,
            verdict              VARCHAR(20) NOT NULL
                CHECK (verdict IN ({_sql_enum_members(Verdict)})),
            audit_event_id       UUID NOT NULL REFERENCES foundation_audit_event(id),
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE md_candle (
            venue          VARCHAR(20) NOT NULL
                CHECK (venue IN ({_sql_enum_members(Venue)})),
            instrument_id  UUID NOT NULL REFERENCES md_instrument(instrument_id),
            timeframe      VARCHAR(10) NOT NULL
                CHECK (timeframe IN ({_sql_enum_members(Timeframe)})),
            open_time      TIMESTAMPTZ NOT NULL,
            close_time     TIMESTAMPTZ NOT NULL,
            open           NUMERIC(30,10) NOT NULL,
            high           NUMERIC(30,10) NOT NULL,
            low            NUMERIC(30,10) NOT NULL,
            close          NUMERIC(30,10) NOT NULL,
            volume         NUMERIC(30,10) NOT NULL,
            quote_volume   NUMERIC(30,10),
            quality_flags  SMALLINT NOT NULL DEFAULT 0,
            batch_id       UUID NOT NULL REFERENCES md_ingest_batch(id),
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (venue, instrument_id, timeframe, open_time),
            CONSTRAINT ck_md_candle_high_ge_open CHECK (high >= open),
            CONSTRAINT ck_md_candle_high_ge_close CHECK (high >= close),
            CONSTRAINT ck_md_candle_high_ge_low CHECK (high >= low),
            CONSTRAINT ck_md_candle_low_le_open CHECK (low <= open),
            CONSTRAINT ck_md_candle_low_le_close CHECK (low <= close),
            CONSTRAINT ck_md_candle_volume_nonneg CHECK (volume >= 0)
        ) PARTITION BY RANGE (open_time)
        """
    )
    op.execute(
        f"""
        CREATE TABLE md_quarantine_candle (
            venue          VARCHAR(20) NOT NULL
                CHECK (venue IN ({_sql_enum_members(Venue)})),
            instrument_id  UUID NOT NULL REFERENCES md_instrument(instrument_id),
            timeframe      VARCHAR(10) NOT NULL
                CHECK (timeframe IN ({_sql_enum_members(Timeframe)})),
            open_time      TIMESTAMPTZ NOT NULL,
            close_time     TIMESTAMPTZ NOT NULL,
            open           NUMERIC(30,10) NOT NULL,
            high           NUMERIC(30,10) NOT NULL,
            low            NUMERIC(30,10) NOT NULL,
            close          NUMERIC(30,10) NOT NULL,
            volume         NUMERIC(30,10) NOT NULL,
            quote_volume   NUMERIC(30,10),
            quality_flags  SMALLINT NOT NULL DEFAULT 0,
            batch_id       UUID NOT NULL REFERENCES md_ingest_batch(id),
            issue_type     VARCHAR(30) NOT NULL
                CHECK (issue_type IN ({_sql_enum_members(QualityIssueType)})),
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (venue, instrument_id, timeframe, open_time, batch_id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE md_tick (
            venue          VARCHAR(20) NOT NULL
                CHECK (venue IN ({_sql_enum_members(Venue)})),
            instrument_id  UUID NOT NULL REFERENCES md_instrument(instrument_id),
            trade_id       VARCHAR(100) NOT NULL,
            price          NUMERIC(30,10) NOT NULL,
            quantity       NUMERIC(30,10) NOT NULL CHECK (quantity > 0),
            side           VARCHAR(4) NOT NULL CHECK (side IN ('buy','sell')),
            traded_at      TIMESTAMPTZ NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (venue, instrument_id, trade_id, traded_at)
        ) PARTITION BY RANGE (traded_at)
        """
    )
    op.execute(
        f"""
        CREATE TABLE md_quality_issue (
            id           BIGSERIAL PRIMARY KEY,
            batch_id     UUID NOT NULL REFERENCES md_ingest_batch(id),
            type         VARCHAR(30) NOT NULL
                CHECK (type IN ({_sql_enum_members(QualityIssueType)})),
            severity     VARCHAR(10) NOT NULL
                CHECK (severity IN ({_sql_enum_members(Severity)})),
            open_time    TIMESTAMPTZ,
            detail       JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(_ENSURE_PARTITIONS_SQL)
    op.execute("SELECT md_ensure_partitions(3)")

    for table in _ALL_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_APP_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION md_ensure_partitions(int) TO {_APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON md_quality_issue_id_seq TO {_APP_ROLE}")

    for table in _WORM_TABLES:
        for statement in worm_sql(table):
            op.execute(statement)


def downgrade() -> None:
    for table in _WORM_TABLES:
        for statement in worm_drop_sql(table):
            op.execute(statement)

    op.execute("DROP FUNCTION IF EXISTS md_ensure_partitions(int)")
    op.execute("DROP TABLE md_quality_issue")
    op.execute("DROP TABLE md_tick")
    op.execute("DROP TABLE md_quarantine_candle")
    op.execute("DROP TABLE md_candle")
    op.execute("DROP TABLE md_ingest_batch")
