"""CM-8 compliance gate (`foundation_compliance.evaluate_compliance_gate`) --
fail-closed branch coverage. No unit test exists for this function anywhere
in the repo (grepped for `evaluate_compliance_gate` in `tests/` before
writing this file).

Each test monkeypatches the module-level `evaluate_pre_trade` name that
`foundation_compliance.py` imported at module scope, so `mandate_repo` is
never actually read -- `_repo()` hands back a plain `object()` cast to the
port type.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from src.foundation.mandates.application.evaluate_pre_trade import (
    ComplianceBundleInactiveError,
    ComplianceMandateMissingError,
)
from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.ports.repository import MandateRepository
from src.services.order_service import foundation_compliance
from src.services.order_service.foundation_compliance import evaluate_compliance_gate
from src.services.order_service.gate import OrderContext

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _context(user_id: UUID) -> OrderContext:
    return OrderContext(
        user_id=user_id,
        execution_id=1,
        exchange="bitget",
        mandate_revision_id=None,
    )


def _repo() -> MandateRepository:
    # `evaluate_pre_trade` is monkeypatched in every test below, so this
    # repo is never actually read -- a real `MandateRepository` is not needed.
    return cast(MandateRepository, object())


async def test_deny_verdict_forwards_reason_codes_and_decision_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision_id = uuid4()

    async def _fake_evaluate_pre_trade(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            verdict=ComplianceVerdict.DENY,
            reason_codes=("SOME_CM_REASON",),
            compliance_decision_id=decision_id,
        )

    monkeypatch.setattr(foundation_compliance, "evaluate_pre_trade", _fake_evaluate_pre_trade)

    result = await evaluate_compliance_gate(
        _repo(), _context(uuid4()), require_compliance_mandate=True, now=_NOW
    )

    assert result.allowed is False
    assert result.reason_codes == ("SOME_CM_REASON",)
    assert result.compliance_decision_id == decision_id


async def test_allow_verdict_returns_allowed_with_decision_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision_id = uuid4()

    async def _fake_evaluate_pre_trade(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            verdict=ComplianceVerdict.ALLOW,
            reason_codes=(),
            compliance_decision_id=decision_id,
        )

    monkeypatch.setattr(foundation_compliance, "evaluate_pre_trade", _fake_evaluate_pre_trade)

    result = await evaluate_compliance_gate(
        _repo(), _context(uuid4()), require_compliance_mandate=True, now=_NOW
    )

    assert result.allowed is True
    assert result.reason_codes == ()
    assert result.compliance_decision_id == decision_id


async def test_mandate_missing_with_require_flag_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _raise(*args: Any, **kwargs: Any) -> Any:
        raise ComplianceMandateMissingError("tenant")

    monkeypatch.setattr(foundation_compliance, "evaluate_pre_trade", _raise)

    result = await evaluate_compliance_gate(
        _repo(), _context(uuid4()), require_compliance_mandate=True, now=_NOW
    )

    assert result.allowed is False
    assert result.reason_codes == ("CM_MANDATE_MISSING",)
    assert result.compliance_decision_id is not None


async def test_mandate_missing_marker_is_deterministic_per_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raise(*args: Any, **kwargs: Any) -> Any:
        raise ComplianceMandateMissingError("tenant")

    monkeypatch.setattr(foundation_compliance, "evaluate_pre_trade", _raise)

    user_id = uuid4()
    context = _context(user_id)

    denied = await evaluate_compliance_gate(
        _repo(), context, require_compliance_mandate=True, now=_NOW
    )
    allowed = await evaluate_compliance_gate(
        _repo(), context, require_compliance_mandate=False, now=_NOW
    )

    assert denied.allowed is False
    assert allowed.allowed is True
    # The no-mandate marker is a stable uuid5 keyed by tenant_id, not a fresh
    # random uuid4 minted per call -- this is the falsifiable core of the leaf.
    assert denied.compliance_decision_id == allowed.compliance_decision_id


async def test_bundle_inactive_always_denies_regardless_of_require_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raise(*args: Any, **kwargs: Any) -> Any:
        raise ComplianceBundleInactiveError("revision")

    monkeypatch.setattr(foundation_compliance, "evaluate_pre_trade", _raise)
    context = _context(uuid4())

    denied_when_required = await evaluate_compliance_gate(
        _repo(), context, require_compliance_mandate=True, now=_NOW
    )
    denied_when_not_required = await evaluate_compliance_gate(
        _repo(), context, require_compliance_mandate=False, now=_NOW
    )

    for result in (denied_when_required, denied_when_not_required):
        assert result.allowed is False
        assert result.reason_codes == ("CM_BUNDLE_INACTIVE",)
