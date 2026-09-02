"""Performance 방법론 — 버전화·해시(R2).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6.

`pm-v1` 기본값: TWR은 현금흐름 시점마다 하위기간으로 끊어 기하연결한다
(domain/twr.py, L46). MWR은 IRR을 이분법(최대 200회, 허용오차 1e-10)으로
푼다(domain/mwr.py, L46). 무위험수익률은 0으로 고정 — 실제 무위험
벤치마크 연동은 이 리프의 스콥이 아니다(필요해지면 별도 검토, 미리
만들지 않는다는 이 세션의 반복 원칙). 연환산은 항상 호출부가
`periods_per_year`를 명시해야 하고(암묵적 가정 금지), 벤치마크는
statement 기간 시작 시점의 mandate 지정값에 고정한다(기간 중 mandate가
바뀌어도 소급 반영하지 않음 — domain/rules.py의 assert_benchmark_pinned,
L46이 실제로 강제한다).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal

from src.foundation.performance.domain.models import Methodology

MWR_MAX_ITERATIONS = 200
MWR_TOLERANCE = Decimal("1E-10")


def methodology_hash(m: Methodology) -> str:
    """`methodology_hash` 필드 자체는 해시 입력에서 제외한다(자기참조 방지)."""
    payload = {
        "version": m.version,
        "twr_method": m.twr_method,
        "mwr_method": m.mwr_method,
        "risk_free_rate_pct": str(m.risk_free_rate_pct),
        "periods_per_year": m.periods_per_year,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


_provisional = Methodology(
    version="pm-v1",
    methodology_hash="",
    twr_method="PERIOD_LINKED_CASHFLOW_AT_START",
    mwr_method="IRR_BISECTION",
    risk_free_rate_pct=Decimal("0"),
    periods_per_year=252,
)
DEFAULT_METHODOLOGY = replace(_provisional, methodology_hash=methodology_hash(_provisional))
