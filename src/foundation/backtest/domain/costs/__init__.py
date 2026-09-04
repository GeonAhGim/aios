"""BT-8 — 백테스트 비용 2종(펀딩·차입) 패키지.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-8(`domain/costs/{funding,borrow}.py`), §3.4(`BacktestConfigV2.costs`),
§9.5 BT-8(DoD: "일할 계산 정확").

`funding.py`(고정 인터벌 정산)와 `borrow.py`(일할 계산)는 정산 방식이
달라 파일을 나눴지만, 최종 비용의 반올림 규칙만은 이 `round_cost` 하나로
고정한다 — 두 파일이 각자 반올림을 따로 구현하면 같은 입력에도 마지막
자리가 어긋날 수 있다.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

_COST_QUANTIZE_EXPONENT = Decimal("0.00000001")  # 1e-8(소수 8자리) — 거래소 통화 정밀도 상한 관례


def round_cost(value: Decimal) -> Decimal:
    """비용 계산 결과를 소수 8자리로 반올림(HALF_EVEN, 은행가 반올림)한다.

    `funding.py`·`borrow.py`가 최종 반환 직전 이 함수 하나만 거치게 해
    반올림 로직 중복 구현을 막는다.
    """

    return value.quantize(_COST_QUANTIZE_EXPONENT, rounding=ROUND_HALF_EVEN)
