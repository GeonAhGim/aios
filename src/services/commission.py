"""13.7 — P2P 중개수수료 계산.

Spec: 기능설계문서_v1.20.md#FD-13.7

핵심 구분(2026-08-10 확정): 이건 매매 성과에 연동된 성과보수(Performance
Fee)가 아니다(자본시장법 투자일임업 인가 문제로 이미 제외된 카테고리) —
"전략 판매 거래 1건이 성사될 때" 판매가격의 일정 비율을 취득하는 플랫폼
중개수수료(App Store식 거래 수수료와 동일 구조)다. 이후 그 전략이 매매로
얼마를 벌든 잃든 이 계산에 영향을 주지 않는다.

DEFAULT_COMMISSION_RATE는 Draft(10~20% 범위 중간값) — 사업성 검토 후
확정될 값이며, 지금은 착수 가능한 합리적 기본값이다.
"""
from __future__ import annotations

from decimal import Decimal

DEFAULT_COMMISSION_RATE = Decimal("0.15")  # Draft — 10~20% 범위 중간값


def calculate_commission(
    price_paid: Decimal | None, rate: Decimal = DEFAULT_COMMISSION_RATE
) -> tuple[Decimal | None, Decimal | None]:
    """(platform_commission_amount, seller_payout_amount)를 반환한다.
    price_paid가 없으면(무료 리스팅 등) 계산할 대상이 없어 둘 다 None."""
    if price_paid is None:
        return None, None
    commission_amount = price_paid * rate
    seller_payout_amount = price_paid - commission_amount
    return commission_amount, seller_payout_amount
