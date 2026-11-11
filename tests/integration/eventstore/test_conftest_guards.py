"""task-23372 DEEPEN: `tests/integration/eventstore/conftest.py` 가드의 negative·실패주입 증빙.

`conftest.py`의 negative/실패주입 섹션은 헤더만 있고 비어 있었다(task-9233 DEEPEN 미완).
`__init__.py`는 빈 패키지 마커라 pytest가 테스트를 수집하지 않으므로, 증빙은 sibling 파일에 둔다.

1. `_replay_clone_id`: 등록되지 않은 replay 모듈은 `ValueError`로 거부한다.
2. `ledger_control`: `CHECK (id = 1)` — 두 번째 행 삽입을 거부한다.
3. `ledger_control.unfrozen_by`: 존재하지 않는 사용자를 FK로 거부한다.
4. 실패주입: `DATABASE_URL` 부재를 `KeyError`로 전파한다(기본값으로 조용히 폴백하지 않는다).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import cast

import asyncpg
import pytest

from tests.integration.eventstore import conftest as eventstore_conftest


def _request_for_module(module_name: str) -> pytest.FixtureRequest:
    module = SimpleNamespace(__name__=module_name)
    return cast(pytest.FixtureRequest, SimpleNamespace(module=module))


def test_unregistered_replay_module_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    request = _request_for_module("tests.integration.eventstore.test_not_registered")

    with pytest.raises(ValueError, match="no replay clone suffix registered"):
        eventstore_conftest._replay_clone_id(request)


def test_registered_replay_module_gets_distinct_clone_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    # 양성 대조군: 등록된 모듈은 거부되지 않고, 모듈별로 서로 다른 clone 이름을 받는다.
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    ids = {
        eventstore_conftest._replay_clone_id(
            _request_for_module(f"tests.integration.eventstore.{module}")
        )
        for module in eventstore_conftest._REPLAY_CLONE_SUFFIX
    }

    assert ids == {"gw0_rv", "gw0_rvc", "gw0_rvt"}


async def test_ledger_control_rejects_second_singleton_row(pool: asyncpg.Pool) -> None:
    # CHECK (id = 1): 두 번째 행이 들어가면 clean-slate 리셋 대상이 유일하지 않게 된다.
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("INSERT INTO ledger_control (id, write_frozen) VALUES (2, FALSE)")


async def test_ledger_control_unfrozen_by_rejects_unknown_user(pool: asyncpg.Pool) -> None:
    # FK(users.user_id): 존재하지 않는 해제자는 감사 추적의 고아 참조이므로 거부한다.
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "UPDATE ledger_control SET unfrozen_by = $1 WHERE id = 1",
                uuid.uuid4(),
            )


def test_missing_database_url_propagates_keyerror(monkeypatch: pytest.MonkeyPatch) -> None:
    # 실패주입: DATABASE_URL이 없으면 기본 DB로 조용히 폴백하지 않고 KeyError로 실패해야 한다.
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(KeyError, match="DATABASE_URL"):
        eventstore_conftest._asyncpg_dsn()
