"""멱등 스코프·digest·client id 단위테스트 — L4-03. DB 없음."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderType
from src.services.oms.contracts.v1_commands import IdempotencyScope, SubmitOrderCommand
from src.services.oms.domain.idempotency import (
    build_scope,
    client_order_id,
    command_digest,
    scope_hash,
)


def _scope(**overrides: Any) -> IdempotencyScope:
    defaults: dict[str, Any] = {
        "tenant_id": uuid4(),
        "account_ref": "acct-1",
        "provider": "bitget",
        "strategy_id": "s1",
        "strategy_version": "1.0.0",
        "execution_id": 1,
        "intent_seq": 1,
        "window_start": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return build_scope(**defaults)


def _command(**overrides: Any) -> SubmitOrderCommand:
    defaults: dict[str, Any] = {
        "command_id": uuid4(),
        "trace_id": uuid4(),
        "scope": _scope(),
        "symbol": "BTC/USDT",
        "side": OrderSide.BUY,
        "order_type": OrderType.MARKET,
        "quantity": Decimal("0.01"),
        "price": None,
        "asset_class": AssetClass.CRYPTO,
        "actor_subject_id": uuid4(),
        "issued_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return SubmitOrderCommand(**defaults)


def test_scope_hash_is_deterministic() -> None:
    tenant_id = uuid4()
    scope_a = _scope(tenant_id=tenant_id)
    scope_b = _scope(tenant_id=tenant_id)
    assert scope_hash(scope_a) == scope_hash(scope_b)


def test_scope_hash_is_64_hex_chars() -> None:
    h = scope_hash(_scope())
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_scope_hash_differs_when_intent_seq_differs() -> None:
    tenant_id = uuid4()
    a = scope_hash(_scope(tenant_id=tenant_id, intent_seq=1))
    b = scope_hash(_scope(tenant_id=tenant_id, intent_seq=2))
    assert a != b


def test_scope_hash_differs_across_tenants() -> None:
    a = scope_hash(_scope(tenant_id=uuid4()))
    b = scope_hash(_scope(tenant_id=uuid4()))
    assert a != b


_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def test_client_order_id_is_deterministic() -> None:
    tenant_id = uuid4()
    a = client_order_id(_scope(tenant_id=tenant_id), max_len=40, charset=_ALNUM)
    b = client_order_id(_scope(tenant_id=tenant_id), max_len=40, charset=_ALNUM)
    assert a == b


def test_client_order_id_respects_max_len_and_charset() -> None:
    result = client_order_id(_scope(), max_len=12, charset=_ALNUM)
    assert len(result) <= 12
    assert all(c in _ALNUM for c in result)


def test_client_order_id_rejects_invalid_params() -> None:
    with pytest.raises(ValueError):
        client_order_id(_scope(), max_len=0, charset="ABC")
    with pytest.raises(ValueError):
        client_order_id(_scope(), max_len=10, charset="")


def test_command_digest_stable_across_retry_metadata() -> None:
    """command_id/trace_id/actor_subject_id/issued_at만 다르면 같은
    재시도로 취급 — digest는 동일해야 한다."""
    cmd_a = _command()
    cmd_b = _command(command_id=uuid4(), trace_id=uuid4(), issued_at=datetime.now(timezone.utc))
    assert command_digest(cmd_a) == command_digest(cmd_b)


def test_command_digest_differs_when_quantity_differs() -> None:
    cmd_a = _command(quantity=Decimal("0.01"))
    cmd_b = _command(quantity=Decimal("0.02"))
    assert command_digest(cmd_a) != command_digest(cmd_b)
