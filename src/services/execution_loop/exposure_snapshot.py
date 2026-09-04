"""R-27 §3.5 노출 스냅샷 SQL — 단일 왕복(R11 지연 예산의 핵심).

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.5, §9 R-27.

§2 표의 `load_exposure_snapshot(conn, *, user_id, execution_id, symbol,
prices)` 시그니처는 §3.5 SQL이 실제로 쓰는 파라미터($1~$6: user_id,
prices, execution_id, strategy_id, symbol, exchange/provider)를 전부
담지 못한다 — strategy_id·provider를 필수 키워드 인자로 추가했다(둘 다
호출부가 이미 갖고 있는 값: OrderIntent.strategy_id, provider_code).

`ExposureSnapshot`은 `core/risk/inputs.py` §3.2의 타입이 아니라 이 모듈에
로컬로 정의한다 — task decision: "필드명을 §3.5 SELECT 별칭 그대로 둔다"
(R-31이 그대로 소비). §3.5 SQL 원문의
`COALESCE(px.price, o.average_entry_price)`는 jsonb_each_text가 text를
반환해 실제로는 `DatatypeMismatchError`로 죽는다(실 DB로 검증 완료) —
`px.price::numeric` 캐스트만 덧붙였고 그 외 CTE·컬럼 구성은 원문 그대로다.
ASSET_CLASS 집계용 심볼별 소계(`gross_by_symbol`)도 같은 왕복에 얹었다 —
그러지 않으면 각주가 요구하는 심볼별 원자료를 얻으려 두 번째 쿼리가
필요해져 "단일 쿼리" DoD를 어긴다.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.data.models.asset_class import asset_class_for

_SQL = """
WITH open_pos AS (
  SELECT p.symbol, p.strategy_id, p.exchange, p.quantity, p.average_entry_price, p.leverage
  FROM positions p
  WHERE p.user_id = $1 AND p.closed_at IS NULL AND p.quantity <> 0
), priced AS (
  SELECT o.*, COALESCE(px.price::numeric, o.average_entry_price) AS mark,
         o.quantity * COALESCE(px.price::numeric, o.average_entry_price) AS mv
  FROM open_pos o LEFT JOIN jsonb_each_text($2::jsonb) px(symbol, price) ON px.symbol = o.symbol
), trades AS (
  SELECT COUNT(*) FILTER (WHERE created_at >= now() - interval '1 hour')  AS n_1h,
         COUNT(*) FILTER (WHERE created_at >= now() - interval '24 hours') AS n_24h
  FROM orders WHERE execution_id = $3
)
SELECT
  (SELECT COALESCE(SUM(ABS(mv)),0) FROM priced)                        AS gross_tenant,
  (SELECT COALESCE(SUM(mv),0)      FROM priced)                        AS net_tenant,
  (SELECT COALESCE(SUM(ABS(mv)),0) FROM priced WHERE strategy_id = $4) AS gross_strategy,
  (SELECT COALESCE(SUM(ABS(mv)),0) FROM priced WHERE symbol = $5)      AS gross_symbol,
  (SELECT COALESCE(SUM(ABS(mv)),0) FROM priced WHERE exchange = $6)    AS gross_provider,
  (SELECT COALESCE(SUM(quantity),0) FROM priced WHERE symbol = $5)     AS position_quantity,
  (SELECT COUNT(*) FROM priced)                                        AS open_positions_count,
  (SELECT MAX(leverage) FROM priced)                                   AS max_leverage,
  (SELECT n_1h FROM trades) AS trades_1h, (SELECT n_24h FROM trades) AS trades_24h,
  (SELECT circuit_breaker_level FROM system_safety_state WHERE id = 1) AS cb_level,
  (SELECT paused_by FROM strategy_executions WHERE id = $3)            AS paused_by,
  (SELECT level FROM data_distrust_state WHERE exchange = $6 AND symbol = $5) AS distrust_level,
  (SELECT COALESCE(jsonb_object_agg(symbol, sym_gross), '{}'::jsonb)
     FROM (SELECT symbol, (SUM(ABS(mv)))::text AS sym_gross FROM priced GROUP BY symbol) s)
                                                                        AS gross_by_symbol
"""


class ExposureSnapshot(BaseModel, frozen=True):
    """6개 scope(tenant·strategy·symbol·provider·position·asset_class) +
    trades/safety 부가 조회 결과 — 필드명은 §3.5 SELECT 별칭 그대로."""

    as_of: datetime
    gross_tenant: Decimal
    net_tenant: Decimal
    gross_strategy: Decimal
    gross_symbol: Decimal
    gross_provider: Decimal
    gross_asset_class: Mapping[str, Decimal]  # "ASSET_CLASS:<cls>", 미확인 심볼은 ":UNKNOWN"
    position_quantity: Decimal
    open_positions_count: int
    max_leverage: Decimal | None
    trades_1h: int
    trades_24h: int
    cb_level: str | None
    paused_by: str | None
    distrust_level: str | None
    input_refs: tuple[str, ...] = ()  # 가격 근사 시 "mark:entry_fallback"(I-06 fail-closed 증거)


def _encode_prices(prices: Mapping[str, Decimal]) -> str:
    return json.dumps({symbol: str(price) for symbol, price in prices.items()})


async def load_exposure_snapshot(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    execution_id: int,
    symbol: str,
    strategy_id: str,
    provider: str,
    prices: Mapping[str, Decimal],
) -> ExposureSnapshot:
    row = await conn.fetchrow(
        _SQL, user_id, _encode_prices(prices), execution_id, strategy_id, symbol, provider
    )
    if row is None:
        raise RuntimeError("exposure_snapshot: 스칼라 집계 SELECT가 0행을 반환함(있을 수 없음)")

    gross_by_symbol: dict[str, str] = json.loads(row["gross_by_symbol"])
    gross_asset_class: dict[str, Decimal] = {}
    for sym, gross_text in gross_by_symbol.items():
        key = f"ASSET_CLASS:{asset_class_for(sym)}"
        gross_asset_class[key] = gross_asset_class.get(key, Decimal(0)) + Decimal(gross_text)

    input_refs = () if symbol in prices else ("mark:entry_fallback",)

    return ExposureSnapshot(
        as_of=datetime.now(timezone.utc),
        gross_tenant=row["gross_tenant"],
        net_tenant=row["net_tenant"],
        gross_strategy=row["gross_strategy"],
        gross_symbol=row["gross_symbol"],
        gross_provider=row["gross_provider"],
        gross_asset_class=gross_asset_class,
        position_quantity=row["position_quantity"],
        open_positions_count=row["open_positions_count"],
        max_leverage=row["max_leverage"],
        trades_1h=row["trades_1h"],
        trades_24h=row["trades_24h"],
        cb_level=row["cb_level"],
        paused_by=row["paused_by"],
        distrust_level=row["distrust_level"],
        input_refs=input_refs,
    )
