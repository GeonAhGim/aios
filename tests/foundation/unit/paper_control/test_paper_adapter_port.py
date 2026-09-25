"""FND-07 paper adapter port — `PaperExecutionContext`/`PaperOrderAck`/
`PaperExecutionAdapter` coverage.

Spec: AIOSproject 77_paper_execution_control_l3_build_and_operational_specification_v1.0.md §4.
task-4688.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.foundation.paper_control.adapters.fake_paper_adapter import (
    FakePaperExecutionAdapter,
)
from src.foundation.paper_control.domain.models import AdapterProvenance, CredentialClass
from src.foundation.paper_control.ports.paper_adapter import (
    PaperExecutionAdapter,
    PaperExecutionContext,
    PaperOrderAck,
)


def _provenance() -> AdapterProvenance:
    return AdapterProvenance(
        adapter_type="fake-paper-v1",
        credential_class=CredentialClass.PAPER,
        endpoint_classification="SANDBOX",
        provider_sandbox_account_ref="sandbox-acct-1",
    )


def test_paper_execution_context_stores_deployment_id_and_provenance() -> None:
    provenance = _provenance()
    deployment_id = str(uuid4())

    context = PaperExecutionContext(deployment_id=deployment_id, provenance=provenance)

    assert context.deployment_id == deployment_id
    assert context.provenance is provenance


def test_paper_order_ack_stores_provider_order_ref() -> None:
    ack = PaperOrderAck(provider_order_ref="fake-paper-order-abc123")

    assert ack.provider_order_ref == "fake-paper-order-abc123"


def test_paper_execution_context_accepts_empty_deployment_id() -> None:
    """Boundary: the port does no validation — an empty id is stored verbatim,
    not rejected. Validation lives in the application layer, not this port."""
    context = PaperExecutionContext(deployment_id="", provenance=_provenance())

    assert context.deployment_id == ""


def test_paper_order_ack_accepts_empty_provider_order_ref() -> None:
    """Boundary: same no-validation contract as the context — negative-path
    inputs are not caught here, only downstream."""
    ack = PaperOrderAck(provider_order_ref="")

    assert ack.provider_order_ref == ""


def test_paper_execution_adapter_protocol_is_not_runtime_checkable() -> None:
    """Negative: `PaperExecutionAdapter` is a plain `Protocol`, not
    `@runtime_checkable` — `isinstance` against it must fail loudly instead of
    silently structural-typing a bad implementation."""
    with pytest.raises(TypeError):
        isinstance(FakePaperExecutionAdapter(), PaperExecutionAdapter)


@pytest.mark.asyncio
async def test_submit_paper_intent_failure_injection_propagates() -> None:
    """Failure injection: an adapter implementation that fails submit must
    surface the exception through the context/protocol boundary unmodified."""
    adapter: PaperExecutionAdapter = FakePaperExecutionAdapter(fail_submit=True)
    context = PaperExecutionContext(deployment_id=str(uuid4()), provenance=_provenance())

    with pytest.raises(ConnectionError):
        await adapter.submit_paper_intent(context, sequence=1)


@pytest.mark.asyncio
async def test_cancel_paper_order_failure_injection_propagates() -> None:
    """Failure injection: cancel_paper_order must propagate adapter exceptions."""
    adapter: PaperExecutionAdapter = FakePaperExecutionAdapter(fail_cancel=True)
    context = PaperExecutionContext(deployment_id=str(uuid4()), provenance=_provenance())

    with pytest.raises(ConnectionError):
        await adapter.cancel_paper_order(context, provider_order_ref="ref-123")


@pytest.mark.asyncio
async def test_fetch_paper_state_failure_injection_propagates() -> None:
    """Failure injection: fetch_paper_state must propagate adapter exceptions."""
    adapter: PaperExecutionAdapter = FakePaperExecutionAdapter(fail_fetch=True)
    context = PaperExecutionContext(deployment_id=str(uuid4()), provenance=_provenance())

    with pytest.raises(ConnectionError):
        await adapter.fetch_paper_state(context)


@pytest.mark.asyncio
async def test_cancel_paper_order_succeeds_with_valid_ref() -> None:
    """Happy path: cancel_paper_order completes without error."""
    adapter: PaperExecutionAdapter = FakePaperExecutionAdapter()
    context = PaperExecutionContext(deployment_id=str(uuid4()), provenance=_provenance())

    await adapter.cancel_paper_order(context, provider_order_ref="ref-abc123")


@pytest.mark.asyncio
async def test_fetch_paper_state_returns_state_string() -> None:
    """Happy path: fetch_paper_state returns a valid state string."""
    adapter: PaperExecutionAdapter = FakePaperExecutionAdapter()
    context = PaperExecutionContext(deployment_id=str(uuid4()), provenance=_provenance())

    state = await adapter.fetch_paper_state(context)

    assert isinstance(state, str)
