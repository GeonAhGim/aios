"""13.1/13.2 — 마켓플레이스 리스팅의 "3개월 Paper Trading 이력" 게이트(9.5-A 원칙).

listing_service.py의 verify_paper_trading_eligibility DI 콜백 구현체.
strategy_executions(FD-16)에 mode='PAPER'로 최소 3개월 전에 시작된 실행이
있는지를 실제로 조회한다 — 해당하는 실행이 없으면(아직 시작 안 했거나
3개월 미만) 거부한다(fail-closed). 이전 구현(_always_eligible)은 FD-16이
없던 시절 무조건 True를 반환했으나, 지금은 테이블이 존재하므로 실제
이력을 확인한다.

task-1803(리뷰 task-1799 REJECT 후속) 결함 수정 2건:
(1) 조회 자체가 strategy_id+strategy_version만 필터해 타 사용자(리스팅을
    시도하는 판매자가 아닌 다른 유저)의 PAPER 이력으로도 게이트를 통과할
    수 있었다 — strategy_executions.user_id = seller_user_id를 추가해
    리스팅을 시도하는 판매자 본인의 이력만 인정한다.
(2) DB 조회 예외(asyncpg.PostgresError/OSError)를 잡지 않아 장애 시 500이
    그대로 전파되고 "거부"가 보장되지 않았다 — 이제 예외를 잡아 False
    (거부)를 반환하고, 구조화 로그(자격증명·쿼리 원문 없이 strategy_id/
    strategy_version/seller_user_id만)를 남긴다(fail-closed).
"""
from __future__ import annotations

import logging
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)

_MIN_PAPER_TRADING_MONTHS = 3


async def check_paper_trading_eligibility(
    pool: asyncpg.Pool, strategy_id: str, strategy_version: str, seller_user_id: UUID
) -> bool:
    """listing_service.VerifyEligibilityFn 시그니처(strategy_id, strategy_version,
    seller_user_id) -> bool에 맞춰 app 조립 단계에서 functools.partial(check_
    paper_trading_eligibility, pool)로 바인딩해 사용한다(risk_matching.check_
    purchase_risk_warning과 동일 패턴).

    조회 실패는 예외를 전파하지 않고 False(거부)로 fail-closed한다 —
    DB 장애가 "이력 확인 불가"를 "이력 있음"으로 오인시켜 미검증 전략의
    리스팅을 통과시키면 안 되기 때문이다.
    """
    try:
        async with pool.acquire() as conn:
            eligible = await conn.fetchval(
                "SELECT EXISTS ("
                "SELECT 1 FROM strategy_executions "
                "WHERE strategy_id = $1 AND strategy_version = $2 AND user_id = $3 "
                "AND mode = 'PAPER' AND started_at IS NOT NULL "
                "AND started_at <= now() - ($4 * interval '1 month')"
                ")",
                strategy_id,
                strategy_version,
                seller_user_id,
                _MIN_PAPER_TRADING_MONTHS,
            )
    except (asyncpg.PostgresError, OSError):
        logger.exception(
            "check_paper_trading_eligibility: 조회 실패로 fail-closed 거부 "
            "(strategy_id=%s, strategy_version=%s, seller_user_id=%s)",
            strategy_id,
            strategy_version,
            seller_user_id,
        )
        return False
    return bool(eligible)
