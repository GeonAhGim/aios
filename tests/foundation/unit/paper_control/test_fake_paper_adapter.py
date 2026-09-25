"""FND-07 fake paper adapter — `FakePaperExecutionAdapter` coverage.

Spec: AIOSproject 77_paper_execution_control_l3_build_and_operational_specification_v1.0.md §7.
task-4678.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.foundation.paper_control.adapters.fake_paper_adapter import (
    FakePaperExecutionAdapter,
)
from src.foundation.paper_control.domain.models import AdapterProvenance, CredentialClass
from src.foundation.paper_control.ports.paper_adapter import PaperExecutionContext


def _context() -> PaperExecutionContext:
    provenance = AdapterProvenance(
        adapter_type="fake-paper-v1",
        credential_class=CredentialClass.PAPER,
        endpoint_classification="SANDBOX",
        provider_sandbox_account_ref="sandbox-acct-1",
    )
    return PaperExecutionContext(deployment_id=str(uuid4()), provenance=provenance)


@pytest.mark.asyncio
async def test_submit_paper_intent_returns_ack_with_fake_prefix() -> None:
    adapter = FakePaperExecutionAdapter()

    ack = await adapter.submit_paper_intent(_context(), sequence=1)

    assert ack.provider_order_ref.startswith("fake-paper-order-")


@pytest.mark.asyncio
async def test_submit_paper_intent_generates_unique_refs_across_calls() -> None:
    """Negative: two submits with the same context/sequence must not collide
    on provider_order_ref — the adapter derives it from a fresh uuid4 each
    call, not from the (context, sequence) input."""
    adapter = FakePaperExecutionAdapter()
    context = _context()

    first = await adapter.submit_paper_intent(context, sequence=1)
    second = await adapter.submit_paper_intent(context, sequence=1)

    assert first.provider_order_ref != second.provider_order_ref


@pytest.mark.asyncio
async def test_submit_paper_intent_default_fail_submit_is_false() -> None:
    """Negative: constructing without fail_submit must not raise — the
    default posture is success, opt-in failure only via fail_submit=True."""
    adapter = FakePaperExecutionAdapter()

    ack = await adapter.submit_paper_intent(_context(), sequence=0)

    assert ack.provider_order_ref != ""


@pytest.mark.asyncio
async def test_submit_paper_intent_fail_submit_raises_connection_error() -> None:
    """Failure injection: fail_submit=True must raise ConnectionError instead
    of returning a fabricated ack."""
    adapter = FakePaperExecutionAdapter(fail_submit=True)

    with pytest.raises(ConnectionError, match="submit 실패"):
        await adapter.submit_paper_intent(_context(), sequence=1)


@pytest.mark.asyncio
async def test_cancel_paper_order_returns_none_for_unknown_ref() -> None:
    """Negative: cancelling a provider_order_ref the adapter never issued
    must not raise — the fake adapter has no order book to validate against."""
    adapter = FakePaperExecutionAdapter()

    result = await adapter.cancel_paper_order(_context(), provider_order_ref="never-issued")

    assert result is None


@pytest.mark.asyncio
async def test_cancel_paper_order_returns_none_for_empty_ref() -> None:
    """Boundary: an empty provider_order_ref is accepted verbatim, matching
    the port's no-validation contract."""
    adapter = FakePaperExecutionAdapter()

    result = await adapter.cancel_paper_order(_context(), provider_order_ref="")

    assert result is None


@pytest.mark.asyncio
async def test_fetch_paper_state_returns_ok() -> None:
    adapter = FakePaperExecutionAdapter()

    state = await adapter.fetch_paper_state(_context())

    assert state == "OK"


@pytest.mark.asyncio
async def test_fetch_paper_state_ignores_fail_submit_flag() -> None:
    """Negative: fail_submit only affects submit_paper_intent — fetch must
    still succeed even when the adapter is configured to fail submits."""
    adapter = FakePaperExecutionAdapter(fail_submit=True)

    state = await adapter.fetch_paper_state(_context())

    assert state == "OK"
