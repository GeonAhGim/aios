"""L4_compliance_and_regulatory_v1.0.md#9 CM-6 — restricted_list.py unit
tests. §8 boundary table: exact match / non-match / empty list / case
sensitivity / missing required field (fail-closed)."""
from __future__ import annotations

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain.rules.restricted_list import RULE_ID, check


def test_symbol_on_restricted_list_denies():
    hit = check({"restricted_symbols": ("XYZ", "ABC")}, {"symbol": "XYZ"})
    assert hit is not None
    assert hit.rule_id == RULE_ID
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence == {"symbol": "XYZ"}


def test_symbol_not_on_restricted_list_allows():
    hit = check({"restricted_symbols": ("XYZ", "ABC")}, {"symbol": "BTC/USDT"})
    assert hit is None


def test_empty_restricted_list_always_allows():
    hit = check({"restricted_symbols": ()}, {"symbol": "XYZ"})
    assert hit is None


def test_missing_restricted_symbols_param_defaults_to_empty_and_allows():
    hit = check({}, {"symbol": "XYZ"})
    assert hit is None


def test_match_is_case_sensitive():
    """'xyz' != 'XYZ' — no implicit normalization (documented in module
    docstring); ticker case handling is a separate future leaf's scope."""
    hit = check({"restricted_symbols": ("XYZ",)}, {"symbol": "xyz"})
    assert hit is None


def test_missing_symbol_field_fails_closed_with_deny():
    hit = check({"restricted_symbols": ("XYZ",)}, {})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
    assert hit.evidence == {"missing_field": "snapshot.symbol"}


def test_none_symbol_field_fails_closed_with_deny():
    hit = check({"restricted_symbols": ("XYZ",)}, {"symbol": None})
    assert hit is not None
    assert hit.severity == ComplianceVerdict.DENY
