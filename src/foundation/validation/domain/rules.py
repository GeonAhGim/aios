"""Strategy Validation pure domain rules — must be testable as unit tests
without DB/HTTP.

Spec: AIOSproject task-76 §3 (validation policy) / §6 (STR-001 reproducibility).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from src.data.models.market_data import Candle
from src.foundation.validation.domain.check_result import CheckResult
from src.foundation.validation.domain.models import Outcome


def _decide(
    hard_fail_reasons: list[str], obligations: list[str]
) -> tuple[Outcome, list[str], list[str]]:
    """I6: outcome is FAIL iff hard_fail_reasons is non-empty (both
    directions hold in code, spec §4 row I6). Kept as the single place this
    rule is decided -- both `evaluate_validation_policy` (one check) and
    `evaluate_bundle` (a check bundle) delegate here so the two call sites
    can never drift onto different outcome criteria."""
    if hard_fail_reasons:
        return Outcome.FAIL, obligations, hard_fail_reasons
    if obligations:
        return Outcome.PASS_WITH_OBLIGATIONS, obligations, []
    return Outcome.PASS, [], []


def _bar_fingerprint(bar: Candle) -> str:
    return "|".join(
        [
            bar.symbol,
            bar.exchange,
            bar.timeframe,
            bar.open_time.isoformat(),
            str(bar.open),
            str(bar.high),
            str(bar.low),
            str(bar.close),
            str(bar.volume),
        ]
    )


def compute_input_snapshot_hash(
    *,
    fsm_definition: dict[str, Any],
    cost_model: dict[str, Any],
    warmup_bars: int,
    periods_per_year: int,
    initial_equity: Decimal,
    bars: list[Candle],
) -> str:
    """Task-76 §1 "input/config pinned before queue", STR-001 "same source/
    config/input/seed compiles and validates to same ... result hash". FSM
    definition, cost model, or bar sequence changes must produce a different
    hash — this lets us distinguish cache reuse (idempotency) from "really
    changed, needs re-validation" when re-running the same strategy."""
    payload = json.dumps(
        {
            "fsm_definition": fsm_definition,
            "cost_model": cost_model,
            "warmup_bars": warmup_bars,
            "periods_per_year": periods_per_year,
            "initial_equity": str(initial_equity),
            "bars": [_bar_fingerprint(b) for b in bars],
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_result_hash(metrics: dict[str, Any]) -> str:
    payload = json.dumps(metrics, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_HARD_FAIL_MARKERS: tuple[str, ...] = (
    # Task-76 §3 R5/point-in-time — actual state mismatch during replay
    # (FD-8.1/8.2 logic bug). run_backtest() swallows exceptions and
    # leaves them as warnings (does not die immediately like
    # BacktestRunError), so we must catch them here.
    "PortfolioEngine 예외",
    # Task-76 §3 robustness row — L34 (overfitting.py) emits a warning
    # containing this string when computation starts (ofit-v1). The
    # calculator does not exist yet so we cannot reach this, but we open
    # the rule table in advance so wiring later is enough (same as
    # task-107 contract stability principle: prevents "quiet bypass").
    "DSR",
    "PBO",
    # Task-76 §3 point-in-time row — look-ahead / survivorship data leak.
    "LOOKAHEAD",
    "데이터 누수",
)


def _is_hard_fail(warning: str) -> bool:
    upper = warning.upper()
    return any(marker.upper() in upper for marker in _HARD_FAIL_MARKERS)


def evaluate_validation_policy(
    warnings: list[str],
) -> tuple[Outcome, list[str], list[str]]:
    """Task-76 §3 "only explicit obligations may be carried to package state" —
    elevate only warnings from the engine (FND-10) that do NOT match
    `_HARD_FAIL_MARKERS` to obligations, and classify the rest
    (backtest errors, DSR/PBO threshold misses, data leaks, etc.) as
    hard_fail_reasons returning FAIL — minimal implementation of I-07
    "hard-fail conditions of the validation/approval gate must be computed
    by domain code and actually return FAIL". If `run_backtest()` itself
    fails (e.g. insufficient warmup), that is an execution failure before
    any policy decision, so this function is never reached (the application
    layer handles it as a separate FAILED)."""
    hard_fail_reasons = [w for w in warnings if _is_hard_fail(w)]
    obligations = [w for w in warnings if not _is_hard_fail(w)]
    return _decide(hard_fail_reasons, obligations)


def evaluate_bundle(results: Sequence[CheckResult]) -> tuple[Outcome, list[str], list[str]]:
    """L4_strategy_portfolio_backtest_v1.0.md §9 L42 — bundle-level I6
    (outcome is FAIL iff hard_fail_reasons is non-empty). Each
    `CheckResult` in `results` comes from one of the six required checks
    (point_in_time, backtest, oos_walk_forward, robustness, stress_capacity,
    failure_conditions -- `policy.REQUIRED_CHECKS`); this function unions
    their `hard_fail_reasons`/`obligations` and re-derives the bundle
    outcome from that union via `_decide`. It deliberately ignores each
    `CheckResult.outcome` field -- a check module that mislabels its own
    outcome (e.g. `PASS` with a populated `hard_fail_reasons`, a bug this
    module cannot prevent upstream) must not be able to launder a hard fail
    past the bundle by way of a wrong `outcome` value; only the reason
    lists are trusted."""
    hard_fail_reasons: list[str] = []
    obligations: list[str] = []
    for result in results:
        hard_fail_reasons.extend(result.hard_fail_reasons)
        obligations.extend(result.obligations)
    return _decide(hard_fail_reasons, obligations)
