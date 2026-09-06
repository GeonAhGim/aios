"""13.1/13.2 — 마켓플레이스 리스팅의 "3개월 Paper Trading 이력" 게이트(9.5-A 원칙).

listing_service.py의 verify_paper_trading_eligibility DI 콜백 구현체.
strategy_executions(FD-16)에 mode='PAPER'로 최소 3개월 전에 시작된 실행이
있는지를 실제로 조회한다 — 해당하는 실행이 없으면(아직 시작 안 했거나
3개월 미만) 거부한다(fail-closed). 이전 구현(_always_eligible)은 FD-16이
없던 시절 무조건 True를 반환했으나, 지금은 테이블이 존재하므로 실제
이력을 확인한다.
"""
from __future__ import annotations

import asyncpg

_MIN_PAPER_TRADING_MONTHS = 3


async def check_paper_trading_eligibility(
    pool: asyncpg.Pool, strategy_id: str, strategy_version: str
) -> bool:
    """listing_service.VerifyEligibilityFn 시그니처(strategy_id, strategy_version)
    -> bool에 맞춰 app 조립 단계에서 functools.partial(check_paper_trading_
    eligibility, pool)로 바인딩해 사용한다(risk_matching.check_purchase_risk_
    warning과 동일 패턴)."""
    async with pool.acquire() as conn:
        eligible = await conn.fetchval(
            "SELECT EXISTS ("
            "SELECT 1 FROM strategy_executions "
            "WHERE strategy_id = $1 AND strategy_version = $2 "
            "AND mode = 'PAPER' AND started_at IS NOT NULL "
            "AND started_at <= now() - ($3 * interval '1 month')"
            ")",
            strategy_id,
            strategy_version,
            _MIN_PAPER_TRADING_MONTHS,
        )
    return bool(eligible)
