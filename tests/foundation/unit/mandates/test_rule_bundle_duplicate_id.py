"""L4_compliance_and_regulatory_v1.0.md#9 CM-3 — unit tests for duplicate rule-id rejection.

Split from test_rule_bundle_evaluator.py to reduce parallel-CI contention
(ProcessPoolExecutor in the same file can starve perf_budget wall-clock
measurements).  This file is intentionally small: one responsibility —
RuleBundle must reject bundles with duplicate rule_id values.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.foundation.mandates.contracts.v1 import ComplianceVerdict, RuleHit
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec


def _allow(_params: Any, _snapshot: Any) -> None:
    return None


def _deny(_params: Any, _snapshot: Any) -> RuleHit | None:
    return RuleHit(rule_id="R_DUP", severity=ComplianceVerdict.DENY, message="deny", evidence={})


def test_duplicate_rule_id_rejected():
    """RuleBundle은 동일한 rule_id을 가진 규칙 두 개를 번들에 담으면
    ValueError를.raise한다 — 중복 ID는 즉시 거부(fail-closed)."""
    dup_a = RuleSpec(rule_id="R_DUP", params={}, check=_allow)
    dup_b = RuleSpec(rule_id="R_DUP", params={}, check=_deny)

    with pytest.raises(ValueError, match="unique"):
        RuleBundle(version="v1", rules=(dup_a, dup_b))
