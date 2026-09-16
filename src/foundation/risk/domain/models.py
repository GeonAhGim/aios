"""U-15 PERSONAL 모드 순수 도메인 모델 — I/O 없음, Decimal만 쓴다.

Spec: task-2749, ADR-2026-09-09-B Decision C 확장.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

PERSONAL_CONSERVATIVE_BUNDLE_NAME = "personal-conservative"


@dataclass(frozen=True)
class PersonalRiskBundle:
    """`config/risk_policy/personal-conservative.yaml`의 도메인 표현.

    `symbol_whitelist`가 비어 있으면 그 어떤 심볼도 허용되지 않는다
    (fail-closed) — "신규 심볼 화이트리스트"는 운영자가 명시적으로 등록한
    심볼만 거래를 허용한다는 뜻이다.
    """

    name: str
    position_pct_of_equity: Decimal
    daily_loss_kill_pct: Decimal
    max_exposure_pct: Decimal
    default_notional_cap_krw: Decimal
    symbol_whitelist: frozenset[str]
    exchange_notional_caps: Mapping[str, Decimal]

    def notional_cap_for(self, exchange: str) -> Decimal:
        return self.exchange_notional_caps.get(exchange, self.default_notional_cap_krw)
