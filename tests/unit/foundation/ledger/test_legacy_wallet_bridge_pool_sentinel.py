"""H-12 — `legacy_wallet_bridge`의 pool placeholder가 실제로 접근되면
즉시 명시적 예외로 실패하는지 확인한다(ADR-2026-09-09-B H-12).

Spec: docs/design/ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md H-12
"주입 경로 정식화 또는 접근 시 즉시 실패하는 sentinel".
"""

from __future__ import annotations

import pytest

from src.foundation.ledger.adapters import legacy_wallet_bridge as bridge_module
from src.foundation.ledger.adapters.legacy_wallet_bridge import (
    _UNUSED_POOL,
    PoolPlaceholderAccessedError,
)


def test_pool_placeholder_raises_on_attribute_access():
    """sentinel의 아무 속성이나 읽으면 `None`의 불명확한 AttributeError
    대신 `PoolPlaceholderAccessedError`로 즉시·명시적으로 실패해야 한다."""
    with pytest.raises(PoolPlaceholderAccessedError, match="pool.acquire"):
        _ = _UNUSED_POOL.acquire


def test_pool_placeholder_error_message_names_the_attribute():
    with pytest.raises(PoolPlaceholderAccessedError, match="pool.execute"):
        _ = _UNUSED_POOL.execute


def test_assembly_points_construct_without_touching_pool():
    """모듈 조립부 3곳(`_journal`/`_balances`/`_audit`)이 sentinel을 주입받아도
    `__init__`이 `self._pool = pool` 대입만 하므로 임포트 시점에 예외가 나지
    않아야 한다 — 이미 성공적으로 임포트됐다는 사실 자체가 회귀 증거다."""
    assert bridge_module._journal is not None
    assert bridge_module._balances is not None
    assert bridge_module._audit is not None
    assert bridge_module._journal._pool is _UNUSED_POOL
    assert bridge_module._balances._pool is _UNUSED_POOL
    assert bridge_module._audit._pool is _UNUSED_POOL


def test_assembly_points_pool_attribute_still_fails_closed_if_touched():
    """조립부가 만든 각 어댑터의 `_pool`도 결국 같은 sentinel이므로, 어댑터가
    리팩터되어 `self._pool`을 실제로 쓰게 되면 세 곳 모두 동일하게
    `PoolPlaceholderAccessedError`로 즉시 드러나야 한다(회귀 가드)."""
    with pytest.raises(PoolPlaceholderAccessedError):
        _ = bridge_module._journal._pool.acquire
    with pytest.raises(PoolPlaceholderAccessedError):
        _ = bridge_module._balances._pool.acquire
    with pytest.raises(PoolPlaceholderAccessedError):
        _ = bridge_module._audit._pool.acquire
