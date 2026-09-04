"""DC-8 — coverage_spans(EXCLUDE)/entitlements.

Revision ID: 9049e2b6b0b7
Revises: dbaf260f2917
Create Date: 2026-09-04 09:13:34.525755

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-8, §4.1(fail-closed 불변조건), §6(커버리지 밖 구간은
DATA_COVERAGE_MISSING, 0/NaN 금지), §9.2 DC-8.

`coverage_spans`는 `ports/coverage_repository.py`(DC-5, task-1126)의
`CoverageSpan` Protocol 계약(instrument_id·venue·timeframe·quality·start·end)
그대로 컬럼을 맞춘다 — 그 포트가 이미 저장 계약이라고 명시한다(모듈
docstring: "CoverageSpan은... 이 포트가 저장 계약으로 직접 정의한다").
`contracts/v2/coverage.py`(DC-6, task-1127)가 별도로 `asset_class`·
`quality_grade`(3단계)를 가진 동명의 `CoverageSpan`을 이미 갖고 있지만,
DC-5 포트가 먼저 병합돼 그 Protocol 시그니처가 저장 계약의 SSOT다 — 이
리프의 decision(task-1195)이 "DC-5 Protocol을 재정의 없이 구현"하라고
명시하므로 포트의 필드 집합을 그대로 따른다(두 계약을 통합하는 것은 이
리프 범위 밖).

겹침 금지 축은 (instrument_id, venue, timeframe, quality) — 같은 축 안의
`[start, end)` 겹침만 DB가 거부한다. 다른 venue/quality의 독립적 선언(예:
같은 기간을 RAW 벤더와 VALIDATED 벤더가 각자 선언)은 서로 겹쳐도 된다
(DC-6 `domain/coverage/registry.py`의 축 정의와 동일 원칙 — 축이 다르면
병합·배제 대상이 아니다).

`entitlements`는 DC-9(`domain/entitlement/policy.py`, task-1179)가 아직
없어 판정 로직은 없다 — 이 리프는 저장 스키마만 만든다(decision: "entitlement
판정 로직은 DC-9 소관이니 여기서는 저장·조회만 한다"). §2.1 DC-9 설명
"테넌트/사용자 라이선스 → 허용 벤처·TF·지연(delayed/realtime) 판정"을 그대로
컬럼으로 옮긴다: tenant_id·subject_id·venue·timeframe·feed_type
(REALTIME/DELAYED)·delayed_seconds·expires_at. 이 리프는 이 테이블을 쓰는
어댑터를 만들지 않는다(decision: "Postgres 어댑터 2종" — instruments/
coverage_repository만).

`btree_gist`는 DC-4(dbaf260f2917)가 이미 CREATE했지만 이 마이그레이션이
독립적으로 실행될 가능성을 고려해 `IF NOT EXISTS`로 재확인한다.
downgrade는 확장을 DROP하지 않는다(dbaf260f2917과 동일 정책 — 다른
객체가 이미 의존할 수 있는 공유 확장을 리프 하나가 되돌리는 건 안전하지
않다).
"""
from collections.abc import Sequence
from enum import Enum

from alembic import op

from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.ports.coverage_repository import CoverageQuality

# revision identifiers, used by Alembic.
revision: str = "9049e2b6b0b7"
down_revision: str | Sequence[str] | None = "dbaf260f2917"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"

_FEED_TYPES = ("REALTIME", "DELAYED")


def _sql_enum_members(enum_cls: type[Enum]) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_cls)


def _sql_str_members(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.execute(
        f"""
        CREATE TABLE coverage_spans (
            span_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            instrument_id  VARCHAR(26) NOT NULL REFERENCES instruments(instrument_id),
            venue          VARCHAR(20) NOT NULL
                CHECK (venue IN ({_sql_enum_members(Venue)})),
            timeframe      VARCHAR(10) NOT NULL
                CHECK (timeframe IN ({_sql_enum_members(Timeframe)})),
            quality        VARCHAR(20) NOT NULL
                CHECK (quality IN ({_sql_enum_members(CoverageQuality)})),
            start_at       TIMESTAMPTZ NOT NULL,
            end_at         TIMESTAMPTZ NOT NULL,
            CHECK (end_at > start_at),
            EXCLUDE USING gist (
                instrument_id WITH =,
                venue WITH =,
                timeframe WITH =,
                quality WITH =,
                tstzrange(start_at, end_at) WITH &&
            )
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE entitlements (
            entitlement_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL,
            subject_id       UUID NOT NULL,
            venue            VARCHAR(20) NOT NULL
                CHECK (venue IN ({_sql_enum_members(Venue)})),
            timeframe        VARCHAR(10) NOT NULL
                CHECK (timeframe IN ({_sql_enum_members(Timeframe)})),
            feed_type        VARCHAR(20) NOT NULL
                CHECK (feed_type IN ({_sql_str_members(_FEED_TYPES)})),
            delayed_seconds  INTEGER NOT NULL DEFAULT 0 CHECK (delayed_seconds >= 0),
            granted_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at       TIMESTAMPTZ,
            CHECK (expires_at IS NULL OR expires_at > granted_at),
            UNIQUE (tenant_id, subject_id, venue, timeframe, feed_type)
        )
        """
    )

    for table in ("coverage_spans", "entitlements"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE entitlements")
    op.execute("DROP TABLE coverage_spans")
