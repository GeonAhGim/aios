"""RD-19 — coverage_spans venue/timeframe CHECK 확장(암호화폐 L2 자체 수집기).

Revision ID: 4b19195124bb
Revises: 3819cf8a5373
Create Date: 2026-09-07 00:00:00.000000

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3·D5, docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9(RD-19
자체 배정 — 문서 §9 표에는 없고 §10 "RD-19~"로 예고된 리프, task-1766
decision).

`coverage_spans`(DC-8, 9049e2b6b0b7)의 `venue`/`timeframe` CHECK 제약은
마이그레이션 작성 시점의 `Venue`/`Timeframe` enum 스냅샷으로 고정돼 있다
(`_sql_enum_members` — 살아있는 enum을 매번 다시 읽지 않는다). `contracts/
v1.py`에 새로 추가한 `Venue.{BINANCE,BYBIT,OKX,UPBIT}`·`Timeframe.L2`를
DB가 거부하지 않도록 그 두 CHECK만 새 값 목록으로 교체한다 — 나머지
컬럼·EXCLUDE 제약·`entitlements` 테이블은 이 리프 범위 밖이라 손대지
않는다.

`entitlements`도 같은 `Venue`/`Timeframe` CHECK를 갖지만 이 리프는 L2
수집 세션이 그 테이블에 쓰지 않으므로(coverage_spans만 씀) 건드리지
않는다 — 필요해지면 별도 리프가 확장한다.
"""
from collections.abc import Sequence

from alembic import op

from src.foundation.market_data.contracts.v1 import Timeframe, Venue

revision: str = "4b19195124bb"
down_revision: str | Sequence[str] | None = "3819cf8a5373"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_VENUES = ("BITGET", "KIS_KRX", "KIS_US")
_OLD_TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")


def _sql_members(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute("ALTER TABLE coverage_spans DROP CONSTRAINT coverage_spans_venue_check")
    op.execute(
        "ALTER TABLE coverage_spans ADD CONSTRAINT coverage_spans_venue_check "
        f"CHECK (venue IN ({_sql_members(tuple(m.value for m in Venue))}))"
    )
    op.execute("ALTER TABLE coverage_spans DROP CONSTRAINT coverage_spans_timeframe_check")
    op.execute(
        "ALTER TABLE coverage_spans ADD CONSTRAINT coverage_spans_timeframe_check "
        f"CHECK (timeframe IN ({_sql_members(tuple(m.value for m in Timeframe))}))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE coverage_spans DROP CONSTRAINT coverage_spans_timeframe_check")
    op.execute(
        "ALTER TABLE coverage_spans ADD CONSTRAINT coverage_spans_timeframe_check "
        f"CHECK (timeframe IN ({_sql_members(_OLD_TIMEFRAMES)}))"
    )
    op.execute("ALTER TABLE coverage_spans DROP CONSTRAINT coverage_spans_venue_check")
    op.execute(
        "ALTER TABLE coverage_spans ADD CONSTRAINT coverage_spans_venue_check "
        f"CHECK (venue IN ({_sql_members(_OLD_VENUES)}))"
    )
