"""FD-8.3 Correlation Risk 지표 — Phase 1 Draft 사전계산 테이블.

06번 §6.2 화이트리스트 5개 심볼만 대상이라 사전계산 테이블로 Draft
구현이 가능하다(기능설계문서_v1.21.md#FD-8.3 처리단계 7). 실제 상관계수는
가격 히스토리 기반 재계산으로 교체돼야 하는 Draft 수치임을 명시한다.
"""
from __future__ import annotations

from decimal import Decimal

# Draft — 실제 가격 히스토리 기반 재계산 전까지의 근사치. 대각선(자기 자신)은
# 1.0으로 취급(아래 correlation_with 참조), 여기 표에는 넣지 않는다.
_DRAFT_CORRELATION_TABLE: dict[frozenset[str], float] = {
    frozenset({"BTC/USDT", "ETH/USDT"}): 0.85,
    frozenset({"BTC/USDT", "SOL/USDT"}): 0.75,
    frozenset({"BTC/USDT", "XRP/USDT"}): 0.55,
    frozenset({"BTC/USDT", "DOGE/USDT"}): 0.60,
    frozenset({"ETH/USDT", "SOL/USDT"}): 0.78,
    frozenset({"ETH/USDT", "XRP/USDT"}): 0.50,
    frozenset({"ETH/USDT", "DOGE/USDT"}): 0.58,
    frozenset({"SOL/USDT", "XRP/USDT"}): 0.52,
    frozenset({"SOL/USDT", "DOGE/USDT"}): 0.56,
    frozenset({"XRP/USDT", "DOGE/USDT"}): 0.65,
}


def correlation_with(symbol_a: str, symbol_b: str) -> float:
    if symbol_a == symbol_b:
        return 1.0
    return _DRAFT_CORRELATION_TABLE.get(frozenset({symbol_a, symbol_b}), 0.0)


def aggregate_correlated_exposure_pct(
    target_symbol: str,
    *,
    threshold: float,
    positions: list[tuple[str, Decimal]],
    total_equity: Decimal,
) -> Decimal:
    """`positions`는 (symbol, market_value) 목록 — 이 사용자의 다른 실행이
    보유한 포지션 전체(이 심볼 자신의 기존 포지션 포함)."""
    if total_equity <= 0:
        return Decimal("0")

    exposed = sum(
        (
            market_value
            for symbol, market_value in positions
            if correlation_with(target_symbol, symbol) > threshold
        ),
        Decimal("0"),
    )
    return (exposed / total_equity) * Decimal("100")
