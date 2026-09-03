"""LA-16a — 틱 배치 기록 테이블(md_ingest_batch_tick).

Revision ID: 4a1d0c0de009
Revises: 4a1d0c0de008
Create Date: 2026-09-03 10:00:00.000000

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9.2 LA-16(a).

`md_ingest_batch`(LA-11, 4a1d0c0de008)는 `timeframe NOT NULL`이라 틱
배치(타임프레임 개념이 없는 개별 체결 수집)를 기록할 수 없다 — 그래서
전용 테이블을 새로 만든다. `md_tick`(같은 마이그레이션)에는 `batch_id`
컬럼이 없어(파티션 UNIQUE 키 제약, task-450 note) 캔들처럼
`md_candle`/`md_quarantine_candle` 행 수를 세어 verdict를 재구성할 수
없다 — 그래서 이 테이블은 `accepted`/`quarantined`/`rejected` 카운트를
직접 컬럼으로 저장하고, `issues`도 정규화된 `md_quality_issue`(그
테이블의 FK는 `md_ingest_batch(id)`만 받는다) 대신 JSONB로 그대로
직렬화한다(LA-13과의 편차, task-656 note).

WORM은 `md_ingest_batch`와 같은 근거(§4.1 배치 판정은 append-only
감사 대상)로 적용한다. 파티셔닝은 하지 않는다 — `md_ingest_batch`도
배치당 한 행뿐이라 파티션 대상이 아니다.
"""
from collections.abc import Sequence
from enum import Enum

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql
from src.foundation.market_data.contracts.v1 import Venue, Verdict

# revision identifiers, used by Alembic.
revision: str = "4a1d0c0de009"
down_revision: str | Sequence[str] | None = "4a1d0c0de008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_TABLE = "md_ingest_batch_tick"


def _sql_enum_members(enum_cls: type[Enum]) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_cls)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {_TABLE} (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID REFERENCES users(user_id),
            source               VARCHAR(50) NOT NULL,
            venue                VARCHAR(20) NOT NULL
                CHECK (venue IN ({_sql_enum_members(Venue)})),
            instrument_id        UUID NOT NULL REFERENCES md_instrument(instrument_id),
            range_start          TIMESTAMPTZ NOT NULL,
            range_end            TIMESTAMPTZ NOT NULL,
            request_fingerprint  VARCHAR(128) NOT NULL,
            batch_hash           VARCHAR(64) NOT NULL,
            accepted_count       INTEGER NOT NULL DEFAULT 0,
            quarantined_count    INTEGER NOT NULL DEFAULT 0,
            rejected_count       INTEGER NOT NULL DEFAULT 0,
            verdict              VARCHAR(20) NOT NULL
                CHECK (verdict IN ({_sql_enum_members(Verdict)})),
            issues               JSONB NOT NULL DEFAULT '[]'::jsonb,
            audit_event_id       UUID NOT NULL REFERENCES foundation_audit_event(id),
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_md_ingest_batch_tick_range CHECK (range_end >= range_start),
            CONSTRAINT ck_md_ingest_batch_tick_counts_nonneg
                CHECK (accepted_count >= 0 AND quarantined_count >= 0 AND rejected_count >= 0)
        )
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO {_APP_ROLE}")

    for statement in worm_sql(_TABLE):
        op.execute(statement)


def downgrade() -> None:
    for statement in worm_drop_sql(_TABLE):
        op.execute(statement)

    op.execute(f"DROP TABLE {_TABLE}")
