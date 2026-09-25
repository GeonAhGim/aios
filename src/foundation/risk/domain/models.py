"""U-15 PERSONAL mode pure domain models — no I/O, Decimal only.

Spec: task-2749, ADR-2026-09-09-B Decision C extension.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

PERSONAL_CONSERVATIVE_BUNDLE_NAME = "personal-conservative"


@dataclass(frozen=True)
class PersonalRiskBundle:
    """Domain representation of
    `config/risk_policy/personal-conservative.yaml`.

    An empty `symbol_whitelist` means no symbol is allowed at all
    (fail-closed) — the "new-symbol whitelist" only allows trading on
    symbols the operator has explicitly registered.
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
