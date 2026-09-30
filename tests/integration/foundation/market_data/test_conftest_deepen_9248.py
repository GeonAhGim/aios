"""DEEPEN(task-9248) for tests/integration/foundation/market_data/conftest.py.

원 리프 task-6704(고아 산출물 회수 5828, qa-2) 대상 DEEPEN — negative test 0건
(<3), 실패주입/성능단언 마커 없음(task-4084 DEEPEN 기준). `conftest.py`의
실제 로직은 `_asyncpg_dsn()`(DSN 스킴 치환)과 `pool` 픽스처 본문
(`asyncpg.create_pool` 호출 + teardown `close()`)뿐이다 -- `candle_store`/
`batch_repo` 픽스처는 어댑터 생성자에 pool을 그대로 넘기는 순수 wiring이라
별도 로직이 없다. `tests/adversarial/market_data/conftest.py`가 이 파일의
`pool`을 재-export하는 사본에 대해 같은 대상(`_asyncpg_dsn`, `pool` 픽스처
본문)을 이미 커버했으므로(task-7723, `test_conftest_deepen_7723.py`), 여기서는
그 원본 `conftest.py` 자체를 직접 import해 동일 로직을 검증하고 -- 이 파일은
`_asyncpg_dsn` 재-export가 아니라 원본을 담고 있으므로 대상이 다르다 --
`candle_store`/`batch_repo` 픽스처가 받은 pool 인스턴스를 그대로(복사·래핑 없이)
어댑터에 넘기는지까지 추가로 확인한다.

conftest.py 자체는 수정하지 않는다.

INVARIANTS.md 점검: I-01~I-11은 주문 제출/실행-소유권/멱등키/전략 아티팩트/
에이전트 capability 등 실행 경로를 다룬다 -- 이 리프는 테스트 전용 DB 픽스처
헬퍼(운영 코드 경로 아님)라 해당 사항 없음(N/A).
"""

from __future__ import annotations

import os
import time
from typing import Any, cast

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_batch_repository import (
    PostgresBatchRepository,
)
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from tests.integration.foundation.market_data.conftest import (
    _asyncpg_dsn,
    batch_repo,
    candle_store,
    pool,
)

_candle_store_fn: Any = cast(Any, candle_store).__wrapped__
_batch_repo_fn: Any = cast(Any, batch_repo).__wrapped__

_pool_fn: Any = cast(Any, pool).__wrapped__

# ---------------------------------------------------------------------------
# Negative tests -- _asyncpg_dsn must fail closed / must not silently repair
# invariant-violating input.
# ---------------------------------------------------------------------------


class TestAsyncpgDsnNegative:
    def test_raises_when_database_url_is_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DATABASE_URL 미설정 시 하드코딩된 기본 DSN으로 조용히 넘어가지
        않고 `KeyError`로 fail-closed 해야 한다."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(KeyError):
            _asyncpg_dsn()

    def test_does_not_rewrite_a_scheme_it_does_not_recognize(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_asyncpg_dsn`은 URL 파서가 아니라 리터럴 `str.replace`다 --
        `postgresql+asyncpg://`가 아닌 스킴(오탈자 등)은 "친절하게" 고쳐주지
        않고 그대로 통과시켜야 한다. 회귀 시 깨진 환경변수가 그럴듯한 DSN
        뒤에 조용히 숨는다."""
        monkeypatch.setenv("DATABASE_URL", "postgres+asyncpg://user:pw@host/db")
        assert _asyncpg_dsn() == "postgres+asyncpg://user:pw@host/db"

    def test_empty_database_url_yields_empty_dsn_not_a_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """빈 `DATABASE_URL`은 빈 DSN을 만들어야 한다(뒤에서
        `asyncpg.create_pool`이 요란하게 실패한다) -- 조용히 기본 접속지로
        치환되어서는 안 된다."""
        monkeypatch.setenv("DATABASE_URL", "")
        assert _asyncpg_dsn() == ""

    def test_rewrites_every_occurrence_of_the_full_scheme_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`str.replace`는 count 인자가 없어 정확한 `postgresql+asyncpg://`
        마커의 모든 출현을 치환한다(선두 하나만이 아니라) -- 향후 제대로 된
        URL 파서로 바꾸는 것은 의도적 결정이어야지 우연한 동작 변화여서는
        안 된다."""
        dsn = "postgresql+asyncpg://user:pw@host/db?opt=postgresql+asyncpg://nested"
        monkeypatch.setenv("DATABASE_URL", dsn)
        assert _asyncpg_dsn() == "postgresql://user:pw@host/db?opt=postgresql://nested"


# ---------------------------------------------------------------------------
# Failure injection -- the pool fixture body must propagate connection
# failures rather than swallowing them. Also covers candle_store/batch_repo
# wiring correctness (they only store the pool reference, no acquire() of
# their own, so identity is the only regressable behavior there).
# ---------------------------------------------------------------------------


class TestPoolFixtureFailureInjection:
    async def test_create_pool_failure_propagates_fail_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`asyncpg.create_pool`이 실패하면(DB 다운, DSN 오류, 인증 거부)
        `pool` 픽스처는 예외를 삼키지 않고 그대로 테스트에 전파해야 한다 --
        깨진/None pool을 돌려주면 나중 테스트가 "데이터 없음"과 "접속 실패"를
        혼동한다."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pw@host/db")

        async def _boom(*_args: Any, **_kwargs: Any) -> asyncpg.Pool[asyncpg.Connection]:
            raise OSError("connection refused")

        monkeypatch.setattr(asyncpg, "create_pool", _boom)

        agen = _pool_fn()
        with pytest.raises(OSError, match="connection refused"):
            await agen.__anext__()

    async def test_pool_is_closed_on_teardown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """픽스처는 teardown에서 `.close()`를 호출해 커넥션 누수를 막아야
        한다 -- `yield` 뒤 cleanup이 누락되는 회귀가 나면 장시간 스위트에서
        `max_size=16` 커넥션 예산이 고갈된다."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pw@host/db")
        closed = {"called": False}

        class _FakePool:
            async def close(self) -> None:
                closed["called"] = True

        async def _fake_create_pool(*_args: Any, **_kwargs: Any) -> _FakePool:
            return _FakePool()

        monkeypatch.setattr(asyncpg, "create_pool", _fake_create_pool)

        agen = _pool_fn()
        yielded = await agen.__anext__()
        assert isinstance(yielded, _FakePool)
        assert closed["called"] is False

        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()
        assert closed["called"] is True

    def test_candle_store_fixture_wires_the_exact_pool_instance(self) -> None:
        """`candle_store` 픽스처는 `PostgresCandleStore(pool)`만 하는 순수
        wiring이다 -- 모든 쿼리 메서드가 `self._pool`이 아니라 호출자가 넘기는
        `conn`을 쓰므로, 이 픽스처가 저장한 pool 참조가 정확히 픽스처가 받은
        그 인스턴스인지가 유일하게 회귀 가능한 지점이다(복사/래핑/무관한
        pool로 바뀌는 회귀를 잡는다)."""
        fake_pool = object()
        store = cast(PostgresCandleStore, _candle_store_fn(cast(Any, fake_pool)))
        assert store._pool is fake_pool  # noqa: SLF001

    def test_batch_repo_fixture_wires_the_exact_pool_instance(self) -> None:
        """`batch_repo` 픽스처도 동일한 순수 wiring이다 -- `self._pool`은
        저장될 뿐 어떤 메서드도 직접 `acquire()`하지 않으므로(모두 `conn`을
        인자로 받음), 저장된 참조가 픽스처가 받은 pool 그대로인지만 검증
        가능하다."""
        fake_pool = object()
        repo = cast(PostgresBatchRepository, _batch_repo_fn(cast(Any, fake_pool)))
        assert repo._pool is fake_pool  # noqa: SLF001


# ---------------------------------------------------------------------------
# Performance assertion -- pool fixture must establish connection within
# budget (ADR-2026-09-09-C).
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_pool_creation_performance_within_budget() -> None:
    """Pool creation(including retry loop) must complete within a reasonable
    latency budget. This prevents accidental O(n) blocking or infinite waits
    during test setup that would accumulate across the suite."""
    start = time.perf_counter()
    agen = _pool_fn()
    p = await agen.__anext__()
    elapsed = time.perf_counter() - start
    await p.close()

    assert elapsed < 10.0, (
        f"pool fixture must establish connection within 10s budget; "
        f"took {elapsed:.2f}s (possible retry loop or network latency)"
    )


# ---------------------------------------------------------------------------
# Gate-red reproduction -- prove the negative test actually catches a
# regression, not just a tautology.
# ---------------------------------------------------------------------------


def test_gate_red_dsn_scheme_rewrite_regression_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """미숙한 "수정"이 도입할 회귀를 시뮬레이션한다: 입력과 무관하게 스킴을
    무조건 `postgresql://`로 치환하면, `+asyncpg` 마커 자체가 없는 등 완전히
    잘못된 `DATABASE_URL`을 눈에 띄게 깨진 채로 두지 않고 조용히 감춘다."""

    def _regressed_dsn() -> str:
        return "postgresql://" + os.environ["DATABASE_URL"].split("://", 1)[-1]

    monkeypatch.setenv("DATABASE_URL", "sqlite:///not-a-postgres-db")
    correct = _asyncpg_dsn()
    regressed = _regressed_dsn()

    assert correct == "sqlite:///not-a-postgres-db"
    assert regressed != correct, (
        "regression would have silently coerced a non-postgres DSN into a "
        "postgresql:// one -- this assertion must fail if that regression lands"
    )
