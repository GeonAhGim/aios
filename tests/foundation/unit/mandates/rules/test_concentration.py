"""L4_compliance_and_regulatory_v1.0.md#9 CM-6 — concentration.py unit
tests. §8 boundary table: below limit / exactly at limit (allow, strict `>`
only) / just above limit (deny) / missing param / missing snapshot field."""
from __future__ import annotations

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain.rules.concentration import RULE_ID, check

_PARAMS = {"max_single_instrument_pct": 25.0}


def test_below_limit_allows():
    hit = check(_PARAMS, {"projected_instrument_pct": 24.9})
    assert hit is None


def test_exactly_at_limit_allows():
    """Boundary case — equal to the limit does not exceed it (strict `>`,
    matching domain/rules.py's existing POLICY_MAX_* checks)."""
    hit = check(_PARAMS, {"projected_instrument_pct": 25.0})
    assert hit is None


def test_just_above_limit_denies():
    hit = check(_PARAMS, {"projected_instrument_pct": 25.1})
    assert hit is not None
    assert hit.rule_id == RULE_ID
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence == {"observed_pct": 25.1, "limit_pct": 25.0}


def test_zero_limit_denies_any_positive_exposure():
    hit = check({"max_single_instrument_pct": 0.0}, {"projected_instrument_pct": 0.1})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY


def test_missing_limit_param_fails_closed_with_deny():
    hit = check({}, {"projected_instrument_pct": 10.0})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence == {"missing_field": "params.max_single_instrument_pct"}


def test_missing_snapshot_field_fails_closed_with_deny():
    hit = check(_PARAMS, {})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence == {"missing_field": "snapshot.projected_instrument_pct"}
