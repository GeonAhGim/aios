"""Pure exclusion-list + leverage rules for the CM-2 7-constraint mandate model.

Spec: docs/specs/L4_compliance_and_regulatory_v1.0.md §9 CM-2 ("extend
constraints on the existing MandateRuleInput (asset class / country /
currency / liquidity / ESG exclusion)").

Deviation from spec text (approved in task-2036 decision): the spec names this
module `mandates/domain/rules/exclusion.py`, but `domain/rules.py` in this repo
is already a module, not a package — promoting it to a package to nest a
sibling would be a pure-rename mass move outside this leaf's scope. This file
lives at `domain/exclusion.py` instead and imports `domain/rules.py` rather
than duplicating its judgement logic (decision: "don't duplicate rules.py's
existing judgement logic — import and use it").

No I/O — every function here takes only `MandateRevision`/`PolicyEvaluationSubject`
value objects and returns plain data (I-01~I-11: pure domain functions, no
network/db/clock calls).
"""
from __future__ import annotations

from src.foundation.mandates.domain.models import (
    MandateRevision,
    PolicyEvaluationSubject,
    PolicyOutcome,
)
from src.foundation.mandates.domain.rules import evaluate_policy


def evaluate_exclusion_lists(
    revision: MandateRevision, subject: PolicyEvaluationSubject
) -> list[str]:
    """5 of the 7 CM-2 constraints: asset class / country / currency / liquidity
    / ESG. Each is list-membership or threshold, evaluated independently and
    collected (not short-circuited) so a subject can report every violated
    constraint at once, matching `rules.evaluate_policy`'s existing pattern."""
    reasons: list[str] = []
    if subject.asset_class is not None and subject.asset_class in revision.excluded_asset_classes:
        reasons.append("POLICY_ASSET_CLASS_EXCLUDED")
    if subject.country is not None and subject.country in revision.excluded_countries:
        reasons.append("POLICY_COUNTRY_EXCLUDED")
    if subject.currency is not None and subject.currency in revision.excluded_currencies:
        reasons.append("POLICY_CURRENCY_EXCLUDED")
    if (
        revision.min_liquidity_score is not None
        and subject.liquidity_score is not None
        and subject.liquidity_score < revision.min_liquidity_score
    ):
        reasons.append("POLICY_LIQUIDITY_BELOW_MINIMUM")
    if subject.asset is not None and subject.asset in revision.esg_excluded_symbols:
        reasons.append("POLICY_ESG_EXCLUDED")
    return reasons


def evaluate_leverage(revision: MandateRevision, subject: PolicyEvaluationSubject) -> list[str]:
    """6th of the 7 constraints: leverage. Kept separate from
    `evaluate_exclusion_lists` because it is a threshold check, not a
    membership check, but folded into the same combined outcome below."""
    if (
        revision.max_leverage_ratio is not None
        and subject.leverage_ratio is not None
        and subject.leverage_ratio > revision.max_leverage_ratio
    ):
        return ["POLICY_MAX_LEVERAGE"]
    return []


def evaluate_mandate_constraints(
    revision: MandateRevision, subject: PolicyEvaluationSubject
) -> tuple[PolicyOutcome, list[str], list[str]]:
    """All 7 CM-2 constraints combined: the 2 `rules.evaluate_policy` already
    expresses that map onto this leaf's list (concentration via
    `max_single_instrument_pct`, plus the pre-existing total exposure/cash
    buffer/daily loss/autonomy/forbidden-asset checks it also runs), and the
    5 exclusion-list + leverage checks this module adds. `PAUSE_REQUIRED` from
    the base evaluation always wins (doc 75 §3 severity order: PAUSE_REQUIRED
    > DENY); the new checks here never raise PAUSE_REQUIRED themselves.
    """
    base_outcome, base_reasons, obligations = evaluate_policy(revision, subject)
    reasons = list(base_reasons)
    reasons.extend(evaluate_exclusion_lists(revision, subject))
    reasons.extend(evaluate_leverage(revision, subject))

    if base_outcome == PolicyOutcome.PAUSE_REQUIRED:
        outcome = PolicyOutcome.PAUSE_REQUIRED
    elif reasons:
        outcome = PolicyOutcome.DENY
    else:
        outcome = PolicyOutcome.ALLOW
    return outcome, reasons, obligations
