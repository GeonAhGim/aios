"""9.2/9.5 — Griefing defense: decides whether an account loss correlates
with a market-wide move.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.3 (`market_correlation.py`),
§4.3 liquidation_request state table, RED_TEAM_FINDINGS RTF-03 (task-1568).

A single pure function. Given the returns of several symbols (a basket), it
decides only whether they "declined simultaneously and meaningfully" —
the correlation-coefficient math itself (Pearson, etc.) belongs to
`risk_stats/correlation_matrix.py` (R-18~20) and is not reimplemented here.
This leaf owns only the narrower boolean decision — "did the basket move
sharply" — that the consumer of the result (watchdog) needs.
"""
from __future__ import annotations

from decimal import Decimal


def is_market_wide_move(
    basket_returns: dict[str, Decimal],
    *,
    account_loss_pct: Decimal,
    min_symbols: int = 3,
    move_threshold_pct: Decimal,
) -> bool | None:
    """If the basket has fewer than `min_symbols`, no decision can be made →
    `None` (the caller treats this the same as "suspected manipulation" and
    errs on the safe side — see `watchdog.decide`).

    Correlation is only considered when an account loss
    (`account_loss_pct`, always >= 0) actually exists — if there is no loss,
    "did the market move correlate with the loss" doesn't even apply, so this
    returns False. If a majority of the basket's symbols declined
    simultaneously by at least `move_threshold_pct`, this is judged a
    market-wide move (a move confined to a single symbol or a minority of
    symbols is left as an isolated loss — treated as suspected manipulation).
    """
    if len(basket_returns) < min_symbols:
        return None
    if account_loss_pct <= 0:
        return False

    declined = sum(1 for r in basket_returns.values() if r <= -move_threshold_pct)
    return declined * 2 > len(basket_returns)
