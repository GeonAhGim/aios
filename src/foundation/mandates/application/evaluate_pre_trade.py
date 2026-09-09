"""L4_compliance_and_regulatory_v1.0.md#9 CM-8 — the sole pre-trade
compliance judgment entry point on the order submission path (§2.2
"application/evaluate_pre_trade.py = 주문 제출 경로에서 호출되는 유일한 사전
판정 진입").

Distinct from `application/evaluate_policy.py` (which evaluates the mandate
revision's *numeric* fields — exposure/autonomy/cash-buffer — against a
`PolicyEvaluationSubject`). This module runs the CM-6/CM-7 *rule-bundle*
checks (`domain/rules/{restricted_list,concentration,leverage}.py`) through
CM-3's `domain/rule_bundle.py`/`domain/evaluator.py` machinery, producing a
`ComplianceDecision` (CM-1 `contracts/v1.py`) — a second, independent
authority from both risk (`src.core.risk`) and the numeric mandate policy
(§0 권위 원칙: "리스크와 컴플라이언스는 분리된 두 권위다").

Known gap (미검증/TODO, out of scope for CM-8): `domain/rules/{liquidity,
position_limit}.py` (CM-7) need `max_pct_of_adv`/`max_position_notional`
params that `MandateRevision` has no field for yet — `assemble_rule_bundle`
accepts them as optional overrides so a future leaf can wire real values in
without touching this module again, but by default they are omitted from
the bundle (never silently run with a missing/zero limit, which would either
false-deny every order or, if defaulted permissively, defeat CM-A2).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from src.core.risk.hashing import canonical_json, sha256_hex
from src.foundation.mandates.contracts.v1 import ComplianceDecision, ComplianceVerdict
from src.foundation.mandates.domain.evaluator import evaluate_bundle
from src.foundation.mandates.domain.models import (
    MandateRevision,
    MandateRevisionState,
    PolicyOutcome,
)
from src.foundation.mandates.domain.models import PolicyBundle as DomainPolicyBundle
from src.foundation.mandates.domain.models import PolicyDecision as DomainPolicyDecision
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec
from src.foundation.mandates.domain.rules import compile_rule_hash, compiler_version
from src.foundation.mandates.domain.rules.concentration import check as _check_concentration
from src.foundation.mandates.domain.rules.leverage import check as _check_leverage
from src.foundation.mandates.domain.rules.liquidity import check as _check_liquidity
from src.foundation.mandates.domain.rules.position_limit import check as _check_position_limit
from src.foundation.mandates.domain.rules.restricted_list import check as _check_restricted_list
from src.foundation.mandates.ports.repository import MandateRepository

DECISION_TTL_SECONDS = 30
"""Matches `evaluate_policy.DECISION_CACHE_TTL_SECONDS` — same "short TTL,
same fingerprint" idempotency contract (§5 "판정: (order_intent_hash,
bundle_version) 멱등"), applied here to `(bundle_hash, inputs_hash)`."""


class ComplianceMandateMissingError(Exception):
    """No `PortfolioMandate`/active revision at all — §3 `CM_MANDATE_MISSING`
    (409). Caller decides fail-closed-vs-passthrough policy (mirrors
    `evaluate_policy.NoActiveMandateError`); this module never defaults."""


class ComplianceBundleInactiveError(Exception):
    """The mandate exists but its active revision is not `ACTIVE` (e.g.
    `PAUSED`/`DRAFT`/`SUPERSEDED`) — §3 `CM_BUNDLE_INACTIVE` (409), §6
    "번들 미활성 → 409 fail-closed(무규칙 통과 금지)". Always fail-closed,
    never gated by a caller flag (unlike mandate-missing)."""


def assemble_rule_bundle(
    revision: MandateRevision,
    snapshot: Mapping[str, Any],
    *,
    max_pct_of_adv: Decimal | None = None,
    max_position_notional: Decimal | None = None,
) -> RuleBundle:
    """CM-6/CM-7 rules, included only when the caller's `snapshot` actually
    carries the data that rule needs. A rule that always fires on a missing
    field would fail-closed-deny *every* order from *every* caller that
    hasn't wired that field yet (today: none of `foundation_gate.py`'s call
    sites compute live `projected_instrument_pct`/`projected_gross_leverage`
    — the same gap `evaluate_policy`'s `PolicyEvaluationSubject` already has
    for its own numeric fields) — that would be a regression, not a control.
    `restricted_list` only needs `snapshot["symbol"]`, which real per-order
    callers do have (`OrderContext.symbol`), so it is the one CM-6/CM-7 rule
    actually wired end-to-end by this leaf; the rest activate automatically,
    with no code change here, once a future leaf's caller starts populating
    the corresponding snapshot key.
    """
    specs: list[RuleSpec] = []
    if "symbol" in snapshot:
        specs.append(
            RuleSpec(
                rule_id="restricted_list",
                params={
                    "restricted_symbols": (
                        *revision.forbidden_assets,
                        *revision.esg_excluded_symbols,
                    )
                },
                check=_check_restricted_list,
            )
        )
    if "projected_instrument_pct" in snapshot:
        specs.append(
            RuleSpec(
                rule_id="concentration",
                params={"max_single_instrument_pct": revision.max_single_instrument_pct},
                check=_check_concentration,
            )
        )
    if revision.max_leverage_ratio is not None and "projected_gross_leverage" in snapshot:
        specs.append(
            RuleSpec(
                rule_id="MANDATE_LEVERAGE_LIMIT",
                params={"max_leverage": Decimal(str(revision.max_leverage_ratio))},
                check=_check_leverage,
            )
        )
    if (
        max_pct_of_adv is not None
        and "order_notional" in snapshot
        and "average_daily_traded_value" in snapshot
    ):
        specs.append(
            RuleSpec(
                rule_id="MANDATE_LIQUIDITY_LIMIT",
                params={"max_pct_of_adv": max_pct_of_adv},
                check=_check_liquidity,
            )
        )
    if max_position_notional is not None and "projected_position_notional" in snapshot:
        specs.append(
            RuleSpec(
                rule_id="MANDATE_POSITION_LIMIT",
                params={"max_position_notional": max_position_notional},
                check=_check_position_limit,
            )
        )
    return RuleBundle(version="cm8/v1", rules=tuple(specs))


_OUTCOME_FOR_VERDICT: dict[ComplianceVerdict, PolicyOutcome] = {
    ComplianceVerdict.ALLOW: PolicyOutcome.ALLOW,
    ComplianceVerdict.DENY: PolicyOutcome.DENY,
    # WARN never fires from today's CM-6/CM-7 rules (all-or-nothing DENY) —
    # kept total for when a future rule introduces it. REQUIRE_APPROVAL is
    # the same collapse CM-1's `verdict_for_outcome` uses in reverse.
    ComplianceVerdict.WARN: PolicyOutcome.REQUIRE_APPROVAL,
}


async def _ensure_bundle_row(
    repo: MandateRepository, revision: MandateRevision
) -> DomainPolicyBundle:
    """`policy_bundle` is UNIQUE(mandate_revision_id) (CM-4) — one artifact
    row per revision, shared with `evaluate_policy`'s numeric-policy compile
    step. This module reuses that row purely as `policy_decision.bundle_id`'s
    FK anchor (no new table, per CM-8 decision note) — the row it points to
    may describe the numeric compiler's `rule_hash`, not this rule-bundle's;
    the compliance decision's *real* content identity is `ComplianceDecision.
    bundle_version` (from `RuleBundle.bundle_hash()`), stored alongside it in
    `policy_decision.command_fingerprint` below, not read back from this row.
    """
    existing = await repo.get_bundle_for_revision(revision.id)
    if existing is not None:
        return existing
    return await repo.insert_policy_bundle(
        DomainPolicyBundle(
            id=UUID(int=0),  # ignored by insert_policy_bundle's INSERT
            mandate_revision_id=revision.id,
            compiler_version=compiler_version(),
            rule_hash=compile_rule_hash(revision),
            created_at=datetime.now(timezone.utc),
        )
    )


def _fingerprint(bundle_version: str, inputs_hash: str) -> str:
    payload = {"bundle_version": bundle_version, "inputs_hash": inputs_hash}
    return sha256_hex(canonical_json(payload))


@dataclass(frozen=True)
class PreTradeResult:
    """What the order-submission path (`foundation_gate.py`) actually needs
    from a pre-trade compliance judgment — the full `ComplianceDecision`
    (rule_hits/evidence) stays inside this module's audit trail
    (`policy_decision`); the gate only needs enough to decide ALLOW/DENY and
    to carry a `compliance_decision_id` through to `submit_order`."""

    verdict: ComplianceVerdict
    reason_codes: tuple[str, ...]
    compliance_decision_id: UUID


async def evaluate_pre_trade(
    repo: MandateRepository,
    *,
    tenant_id: UUID,
    portfolio_id: UUID | None,
    snapshot: Mapping[str, Any],
    now: datetime,
) -> PreTradeResult:
    """Raises `ComplianceMandateMissingError`/`ComplianceBundleInactiveError`
    on fail-closed conditions — same delegation pattern as `evaluate_policy.
    NoActiveMandateError`: this function never decides ALLOW-passthrough
    policy for a missing mandate, the caller does (mirrors `foundation_gate.
    py`'s `require_mandate`)."""
    mandate = await repo.get_mandate(tenant_id, portfolio_id)
    if mandate is None or mandate.active_revision_id is None:
        raise ComplianceMandateMissingError(str(tenant_id))
    revision = await repo.get_revision(mandate.active_revision_id)
    assert revision is not None  # FK guarantees this
    if revision.state != MandateRevisionState.ACTIVE:
        raise ComplianceBundleInactiveError(str(revision.id))

    bundle = assemble_rule_bundle(revision, snapshot)
    inputs_hash = sha256_hex(canonical_json(dict(snapshot)))
    fingerprint = _fingerprint(bundle.bundle_hash(), inputs_hash)

    cached = await repo.get_cached_decision(tenant_id, fingerprint)
    if cached is not None:
        return PreTradeResult(
            verdict=_verdict_for_cached_outcome(cached.outcome),
            reason_codes=cached.reason_codes,
            compliance_decision_id=cached.id,
        )

    decision: ComplianceDecision = evaluate_bundle(bundle, snapshot, now=now)

    bundle_row = await _ensure_bundle_row(repo, revision)
    stored = await repo.insert_policy_decision(
        # `id` below is a required dataclass field but, like `_ensure_bundle_row`'s
        # placeholder, is ignored by the Postgres adapter's INSERT (the DB
        # generates the real primary key) — `stored.id` (not `decision.decision_id`)
        # is what `PreTradeResult.compliance_decision_id` returns below.
        DomainPolicyDecision(
            id=decision.decision_id,
            tenant_id=tenant_id,
            bundle_id=bundle_row.id,
            command_type="PRE_TRADE_COMPLIANCE",
            command_fingerprint=fingerprint,
            outcome=_OUTCOME_FOR_VERDICT[decision.verdict],
            reason_codes=tuple(hit.rule_id for hit in decision.rule_hits),
            obligations=(),
            evaluated_at=decision.evaluated_at,
            expires_at=decision.evaluated_at + timedelta(seconds=DECISION_TTL_SECONDS),
        )
    )
    return PreTradeResult(
        verdict=decision.verdict,
        reason_codes=stored.reason_codes,
        compliance_decision_id=stored.id,
    )


def _verdict_for_cached_outcome(outcome: PolicyOutcome) -> ComplianceVerdict:
    if outcome == PolicyOutcome.ALLOW:
        return ComplianceVerdict.ALLOW
    if outcome == PolicyOutcome.DENY:
        return ComplianceVerdict.DENY
    return ComplianceVerdict.WARN
