"""DEEPEN(task-7706, 원 리프 task-5487): `conftest.py`의 픽스처 헬퍼
(`create_pos_account`/`open_position`/`_asyncpg_dsn`)가 불변식 위반 입력을
명시적으로 거부하는지, 그리고 의존성(커넥션) 실패 시 조용히 삼키지 않고
그대로 전파(fail-closed)하는지를 검증한다.

INVARIANTS.md 점검: I-01~I-11은 주문 제출/실행-소유권/멱등키/전략 아티팩트/
에이전트 capability 등 실행 경로를 다룬다 -- 이 리프는 테스트 전용 DB 픽스처
헬퍼(운영 코드 경로 아님)라 해당 사항 없음(N/A).

성능 테스트는 `test_conftest_deepen_perf.py`로 분리됨 (task-18349).
"""

from __future__ import annotations

import uuid
from uuid import UUID

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import Currency
from src.foundation.positions.contracts.v1 import CostMethod
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import (
    _asyncpg_dsn,
    create_pos_account,
    open_position,
)


class _InjectedConnFailure(RuntimeError):
    pass


class _FailingConn:
    async def fetchval(self, *args: object, **kwargs: object) -> UUID:
        raise _InjectedConnFailure("injected connection failure in fetchval")


class _FailingAcquireCtx:
    async def __aenter__(self) -> _FailingConn:
        return _FailingConn()

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FailingPool:
    """DB 없이도 실행 가능한 의존성 실패 주입용 이중체 -- 실제 asyncpg.Pool을
    대체하지 않고 `acquire()`만 흉내 낸다."""

    def acquire(self) -> _FailingAcquireCtx:
        return _FailingAcquireCtx()


async def test_create_pos_account_propagates_connection_failure() -> None:
    """실패주입: `pool.acquire()` 하위 커넥션의 `fetchval`이 예외를 던지면
    `create_pos_account`는 이를 삼키지 않고 그대로 전파해야 한다(fail-closed)."""
    with pytest.raises(_InjectedConnFailure, match="injected connection failure"):
        await create_pos_account(_FailingPool(), uuid.uuid4())


async def test_asyncpg_dsn_raises_when_database_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative: `DATABASE_URL`이 비어 있으면(설정 누락) 조용히 빈 DSN을
    만들어내지 말고 명시적으로 실패해야 한다."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(KeyError):
        _asyncpg_dsn()


async def test_create_pos_account_rejects_invalid_base_currency(
    pool: asyncpg.Pool,
) -> None:
    """negative: 지원되지 않는 통화 코드는 INSERT 단계에서 즉시 거부해야 한다."""
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(ValueError):
        await create_pos_account(pool, tenant_id, base_currency=Currency("USD"))


async def test_create_pos_account_rejects_invalid_cost_method(
    pool: asyncpg.Pool,
) -> None:
    """negative: 지원되지 않는 원가 계산 방식은 INSERT 단계에서 즉시 거부해야 한다."""
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(ValueError):
        await create_pos_account(pool, tenant_id, cost_method=CostMethod("INVALID"))


async def test_create_pos_account_rejects_unknown_tenant_id(
    pool: asyncpg.Pool,
) -> None:
    """negative: 존재하지 않는 tenant_id 로 pos_account 를 생성하면 FK 제약으로
    실패해야 한다 (fail-closed)."""
    unknown_tenant_id = uuid.uuid4()
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await create_pos_account(pool, unknown_tenant_id)


async def test_open_position_rejects_duplicate_first_creation_call(
    pool: asyncpg.Pool,
) -> None:
    """negative: 같은 position_key 로 `open_position` 을 두 번 호출하면
    `ConcurrencyConflictError` 를 던져야 한다 (idempotency)."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    from src.foundation.entities.domain.defaults import default_portfolio_id
    from src.foundation.positions.domain.position_key import PositionKey

    position_key = str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=f"INST{uuid.uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="paper",
            portfolio_id=default_portfolio_id(tenant_id),
        )
    )
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    with pytest.raises(ConcurrencyConflictError):
        await open_position(
            pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key
        )
