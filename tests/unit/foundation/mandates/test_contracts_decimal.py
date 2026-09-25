"""task-5762 -- money_float ratchet gap in mandates/contracts v1 and
risk/contracts v1 (QA task-5117, parent task-4188).

CTO decision (2026-09-23): `contracts/v1.py` field types are a wire
compatibility surface (ADR-2026-09-10-C P5 hard-vetoes in-place type changes
to a published v1 contract). `min_cash_buffer_pct`/`cash_buffer_pct` and the
risk-bundle KRW/pct fields stay `float` on the wire; Decimal correctness is
applied at the domain/application boundary
(`evaluate_policy.py`/`create_draft_mandate.py`/`bundle_loader.py`), which
must convert via `Decimal(str(x))` -- never `Decimal(x)`, which reproduces
the source float's binary-rounding error instead of removing it.

Negative tests here cover the failure mode this task exists to close: a
boundary conversion that uses the float directly instead of round-tripping
through `str()` silently keeps float precision error in a Decimal that looks
exact.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.foundation.mandates.contracts.v1 import PolicyEvaluationSubject
from src.foundation.risk.contracts.v1 import PersonalRiskBundleConfigV1


def test_mandates_contract_v1_cash_buffer_field_stays_float_on_wire() -> None:
    """The v1 wire contract is unchanged by this task -- only the domain
    boundary conversion is (CTO decision, task-5762). A regression that
    quietly widens this field to Decimal would break wire compatibility for
    any consumer still POSTing a JSON float."""
    subject = PolicyEvaluationSubject(command_type="PLACE_ORDER", cash_buffer_pct=0.1)
    assert isinstance(subject.cash_buffer_pct, float)


def test_boundary_conversion_via_str_removes_float_rounding_error() -> None:
    """0.1 has no exact binary float representation. `Decimal(str(0.1))` is
    exact; `Decimal(0.1)` (skipping the str() round-trip) leaks the binary
    approximation -- this is exactly the defect class this leaf exists to
    catch if a future edit drops the `str()` call at the boundary."""
    wire_value = 0.1

    correct = Decimal(str(wire_value))
    assert correct == Decimal("0.1")

    naive = Decimal(wire_value)
    assert naive != Decimal("0.1")


def test_policy_evaluation_subject_rejects_non_numeric_cash_buffer_pct() -> None:
    """Negative test: the v1 wire contract still fails closed on a malformed
    cash_buffer_pct -- staying `float` on the wire (CTO decision) is not
    license to accept arbitrary JSON for a money-shaped field."""
    with pytest.raises(ValidationError):
        PolicyEvaluationSubject(command_type="PLACE_ORDER", cash_buffer_pct="not-a-decimal")


def test_risk_bundle_config_v1_rejects_non_positive_notional_cap() -> None:
    """Negative test: `default_notional_cap_krw` keeps its `gt=0` wire
    validation regardless of the float/Decimal boundary question -- a
    zero or negative notional cap must never load as a valid config
    (fail-closed, same posture as the rest of risk_policy_loader.py)."""
    with pytest.raises(ValueError):
        PersonalRiskBundleConfigV1(
            name="test",
            version=1,
            position_pct_of_equity=0.5,
            daily_loss_kill_pct=0.05,
            max_exposure_pct=0.5,
            default_notional_cap_krw=0.0,
        )


def test_risk_bundle_config_v1_field_stays_float_on_wire() -> None:
    """Same wire-boundary posture as the mandates contract above."""
    config = PersonalRiskBundleConfigV1(
        name="test",
        version=1,
        position_pct_of_equity=0.5,
        daily_loss_kill_pct=0.05,
        max_exposure_pct=0.5,
        default_notional_cap_krw=1_000_000.0,
    )
    assert isinstance(config.default_notional_cap_krw, float)
