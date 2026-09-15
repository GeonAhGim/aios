"""LB-15 `compute_daily_nav` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-15.
DoD(task-714): "멱등, 체인 위반 거부". `pos_snapshot`/`pos_nav_daily`는
실제 Postgres(LB-9 어댑터, `nav_repo.insert`의 ON CONFLICT+source_hash
비교까지 검증)를 쓰고, 현금 잔고는 이 리프의 관심사가 아닌 미착수
어댑터(`CashSource`, 모듈독스트링 "미검증")라 in-memory fake로 대역한다.

task-2980(DEPTH 감사 task-2723 보강, D1->D3): 원본 커밋(fc73e26)이 가진
4가지 미달분을 이 파일에 추가한다.

1. 진짜 failure-injection — `test_stale_mark_on_open_position_rejects_nav`/
   `test_missing_cash_source_value_rejects_nav`는 포트 계약의 경계값(`None`)
   검증이지 시뮬레이션 예외/DB 오류가 아니다. 아래
   `test_pool_connection_lost_before_write_propagates_and_writes_nothing`은
   실제 asyncpg 예외 클래스(`ConnectionDoesNotExistError`)로 읽기 단계와
   쓰기 단계 사이의 진짜 커넥션 유실을 재현하고, 부분 쓰기가 없음을 실 DB
   조회로 확인한다.
2. 수치 성능 단언 —
   `test_compute_daily_nav_sequential_round_trips_and_latency`가
   `tests/integration/foundation/positions/test_perf_journal_append.py`
   (LB-18)와 동일 기법(asyncpg 쿼리 로거)으로 순차 DB 왕복 수 상한을 건다.
3. 게이트 적색 재현 —
   `test_pytest_gate_turns_red_when_verify_chain_call_is_removed`이
   `tests/integration/foundation/market_data/test_replay_candles.py`
   (task-2972)와 동일 기법(자식 pytest 프로세스에 소스 문자열 치환 주입)으로
   `verify_chain` 호출을 지우면 기존 체인 위반 negative test가 green에서
   red로 뒤집힘을 실측한다.
4. 동시성(asyncio.gather) — 두 개의
   `test_concurrent_*`가 같은 `(account_id, nav_date)`에 실제 동시
   커넥션으로 경합해 (a) 같은 계산의 재시도는 단일 행으로 수렴하고 (b) 서로
   다른 유효 계산이 부딪히면 하나만 저장되고 나머지는 어댑터의 진짜
   `source_hash` 충돌 경로(`postgres_nav_repository.NavChainBrokenError`)로
   거부됨을 증명한다 — 순차 호출로는 드러나지 않는 실제 DB 경합 경로다.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from src.data.models.base import Currency, Money
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.positions.adapters.postgres_nav_repository import (
    NavChainBrokenError as AdapterNavChainBrokenError,
)
from src.foundation.positions.adapters.postgres_nav_repository import PostgresNavRepository
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.compute_daily_nav import (
    ComputeDailyNavCommand,
    NavCashUnavailableError,
    NavMarkUnavailableError,
    compute_daily_nav,
)
from src.foundation.positions.contracts.v1 import CostMethod, NAVSnapshot, PositionSnapshotView
from src.foundation.positions.domain import nav
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account

_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
_BITGET = VenueCalendar(venue="bitget", tz=ZoneInfo("UTC"), regular=KNOWN_SESSIONS["BITGET"])


class FakeCashSource:
    def __init__(self) -> None:
        self._balances: dict[UUID, Decimal | None] = {}

    def seed(self, account_id: UUID, balance: Decimal | None) -> None:
        self._balances[account_id] = balance

    async def cash(self, account_id: UUID, at: datetime) -> Decimal | None:
        return self._balances.get(account_id)


class FakeFxRateSource:
    """이 테스트 스위트는 통화 불일치 케이스를 쓰지 않으므로 호출되면
    그 자체가 결함 신호다."""

    async def rate(self, base: Currency, quote: Currency, at: datetime) -> None:  # pragma: no cover
        raise NotImplementedError(f"FX 경로가 필요 없는 테스트에서 호출됨: {base}->{quote}")


def _unique_symbol(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:8]}"


def _position_key(tenant_id: UUID, venue_symbol: str) -> str:
    return str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
            venue="bitget",
            instrument_id=venue_symbol,
            strategy_id="default",
            execution_id="paper",
        )
    )


async def _open_marked_position(
    pool, *, tenant_id, account_id, quantity: Decimal, mark_price: Money | None
) -> PositionSnapshotView:
    position_key = _position_key(tenant_id, _unique_symbol("BTCUSDT"))
    snapshot = PositionSnapshotView(
        position_key=position_key,
        tenant_id=tenant_id,
        account_id=account_id,
        instrument_id=uuid4(),
        quantity=quantity,
        avg_cost=Money(amount=Decimal("60000"), currency=Currency.USDT),
        cost_method=CostMethod.FIFO,
        lots=[],
        realized_pnl_base=Decimal("0"),
        unrealized_pnl_base=None,
        fees_base=Decimal("0"),
        funding_base=Decimal("0"),
        mark_price=mark_price,
        mark_at=_NOW if mark_price is not None else None,
        base_currency=Currency.USDT,
        last_journal_seq=0,
        updated_at=_NOW,
    )
    repo = PostgresSnapshotRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        return await repo.upsert(conn, snapshot, expected_seq=0)


async def _setup_account(pool) -> tuple[UUID, UUID]:
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(
        pool, tenant_id, venue="bitget", base_currency=Currency.USDT
    )
    return tenant_id, account_id


def _cmd(
    *,
    tenant_id,
    account_id,
    at,
    realized="1000",
    unrealized_delta="0",
    funding="0",
    fees="0",
    flows="0",
):
    return ComputeDailyNavCommand(
        tenant_id=tenant_id,
        account_id=account_id,
        base_currency=Currency.USDT,
        at=at,
        realized=Decimal(realized),
        unrealized_delta=Decimal(unrealized_delta),
        funding=Decimal(funding),
        fees=Decimal(fees),
        flows=Decimal(flows),
        trace_id=uuid4(),
    )


async def test_first_day_has_zero_opening_and_persists(pool):
    tenant_id, account_id = await _setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)

    result = await compute_daily_nav(
        _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW),
        snapshots=PostgresSnapshotRepository(pool),
        cash=cash,
        nav_repo=nav_repo,
        calendar=_BITGET,
        fx=FakeFxRateSource(),
        pool=pool,
    )

    assert result.opening_nav == Decimal("0")
    assert result.closing_nav == Decimal("1000")

    async with pool.acquire() as conn:
        stored = await nav_repo.get(conn, account_id, result.nav_date)
    assert stored is not None
    assert stored.source_hash == result.source_hash


async def test_second_day_chains_off_first_days_closing(pool):
    tenant_id, account_id = await _setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)
    common = dict(
        snapshots=PostgresSnapshotRepository(pool),
        cash=cash,
        nav_repo=nav_repo,
        calendar=_BITGET,
        fx=FakeFxRateSource(),
        pool=pool,
    )

    day1 = await compute_daily_nav(
        _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW), **common
    )
    assert day1.closing_nav == Decimal("1000")

    cash.seed(account_id, Decimal("1030"))
    day2 = await compute_daily_nav(
        _cmd(
            tenant_id=tenant_id,
            account_id=account_id,
            at=_NOW + timedelta(days=1),
            realized="30",
        ),
        **common,
    )

    assert day2.opening_nav == day1.closing_nav
    assert day2.closing_nav == Decimal("1030")


async def test_rerun_same_day_is_idempotent_no_duplicate_row(pool):
    tenant_id, account_id = await _setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("500"))
    nav_repo = PostgresNavRepository(pool)
    cmd = _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW, realized="500")
    common = dict(
        snapshots=PostgresSnapshotRepository(pool),
        cash=cash,
        nav_repo=nav_repo,
        calendar=_BITGET,
        fx=FakeFxRateSource(),
        pool=pool,
    )

    first = await compute_daily_nav(cmd, **common)
    second = await compute_daily_nav(cmd, **common)

    assert first.source_hash == second.source_hash
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM pos_nav_daily WHERE account_id = $1 AND nav_date = $2",
            account_id,
            first.nav_date,
        )
    assert count == 1


async def test_chain_break_when_rollforward_does_not_reconcile_is_rejected(pool):
    """DoD negative: 대차대조(cash+positions_mv=1000)와 롤포워드(realized=1
    뿐이라 0+1=1) 등식이 어긋나면 저장을 거부한다."""
    tenant_id, account_id = await _setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)

    with pytest.raises(nav.NavChainBrokenError):
        await compute_daily_nav(
            _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW, realized="1"),
            snapshots=PostgresSnapshotRepository(pool),
            cash=cash,
            nav_repo=nav_repo,
            calendar=_BITGET,
            fx=FakeFxRateSource(),
            pool=pool,
        )

    async with pool.acquire() as conn:
        stored = await nav_repo.get(conn, account_id, _BITGET.trading_day_of(_NOW))
    assert stored is None, "체인 위반 시도가 행을 저장했습니다"


async def test_stale_mark_on_open_position_rejects_nav(pool):
    """DoD negative: 열린 포지션의 mark_price가 None(스테일)이면 전체 NAV
    산출을 거부한다 — 추정치 대입 금지."""
    tenant_id, account_id = await _setup_account(pool)
    await _open_marked_position(
        pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1"), mark_price=None
    )
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)

    with pytest.raises(NavMarkUnavailableError):
        await compute_daily_nav(
            _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW),
            snapshots=PostgresSnapshotRepository(pool),
            cash=cash,
            nav_repo=nav_repo,
            calendar=_BITGET,
            fx=FakeFxRateSource(),
            pool=pool,
        )

    async with pool.acquire() as conn:
        stored = await nav_repo.get(conn, account_id, _BITGET.trading_day_of(_NOW))
    assert stored is None


async def test_marked_open_position_contributes_to_positions_mv(pool):
    tenant_id, account_id = await _setup_account(pool)
    await _open_marked_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        quantity=Decimal("2"),
        mark_price=Money(amount=Decimal("100"), currency=Currency.USDT),
    )
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("50"))
    nav_repo = PostgresNavRepository(pool)

    result = await compute_daily_nav(
        _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW, realized="250"),
        snapshots=PostgresSnapshotRepository(pool),
        cash=cash,
        nav_repo=nav_repo,
        calendar=_BITGET,
        fx=FakeFxRateSource(),
        pool=pool,
    )

    assert result.positions_mv == Decimal("200")  # 2 * 100
    assert result.closing_nav == Decimal("250")  # cash(50) + mv(200)


async def test_missing_cash_source_value_rejects_nav(pool):
    tenant_id, account_id = await _setup_account(pool)
    nav_repo = PostgresNavRepository(pool)

    with pytest.raises(NavCashUnavailableError):
        await compute_daily_nav(
            _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW),
            snapshots=PostgresSnapshotRepository(pool),
            cash=FakeCashSource(),  # seed 안 함 -> None
            nav_repo=nav_repo,
            calendar=_BITGET,
            fx=FakeFxRateSource(),
            pool=pool,
        )


# ---------- 진짜 failure-injection — 실제 asyncpg 예외로 커넥션 유실 재현 ----------
#
# 위 `test_missing_cash_source_value_rejects_nav`/`test_stale_mark_on_open_position_
# rejects_nav`는 포트 계약의 경계값(`None`) 검증이지 인프라 결함이 아니다. 이
# 테스트는 `compute_daily_nav`가 읽기 단계(1차 `pool.acquire()`)와 쓰기 단계
# (2차 `pool.acquire()`) 사이에서 실제로 두 번 커넥션을 새로 얻는다는 사실을
# 이용해, 두 번째 획득에서 진짜 asyncpg 예외(`ConnectionDoesNotExistError`)를
# 던지는 얇은 래퍼로 실제 커넥션 유실을 재현한다 — 예외가 감싸이지 않고
# 그대로 전파되는지, 그리고 부분 쓰기(고아 행)가 없는지를 실 DB 조회로 확인한다.


class _FlakyPool:
    def __init__(self, pool: asyncpg.Pool, *, fail_at_acquire: int, error: Exception) -> None:
        self._pool = pool
        self._fail_at = fail_at_acquire
        self._error = error
        self._count = 0

    def acquire(self):  # noqa: ANN201 -- returns whatever asyncpg.Pool.acquire() returns
        self._count += 1
        if self._count == self._fail_at:
            raise self._error
        return self._pool.acquire()


async def test_pool_connection_lost_before_write_propagates_and_writes_nothing(pool):
    tenant_id, account_id = await _setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)
    flaky_pool = _FlakyPool(
        pool,
        fail_at_acquire=2,  # 1차 acquire(읽기)는 통과, 2차 acquire(쓰기)에서 유실
        error=asyncpg.exceptions.ConnectionDoesNotExistError("simulated connection loss"),
    )

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await compute_daily_nav(
            _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW),
            snapshots=PostgresSnapshotRepository(pool),
            cash=cash,
            nav_repo=nav_repo,
            calendar=_BITGET,
            fx=FakeFxRateSource(),
            pool=flaky_pool,
        )

    async with pool.acquire() as conn:
        stored = await nav_repo.get(conn, account_id, _BITGET.trading_day_of(_NOW))
    assert stored is None, "커넥션 유실 시도가 고아 행을 남겼습니다"


# ---------- 동시성(asyncio.gather) — 같은 (account_id, nav_date)에 실제 DB 경합 ----------


async def test_concurrent_same_day_retries_converge_to_single_row(pool):
    """같은 커맨드(같은 source_hash)를 진짜 동시 커넥션으로 여러 번 실행해도
    `pos_nav_daily`에는 한 행만 남는다 — 순차 재실행 멱등(위 테스트)과 달리
    실제 DB 레벨 경합에서도 멱등이 유지되는지를 증명한다."""
    tenant_id, account_id = await _setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("777"))
    nav_repo = PostgresNavRepository(pool)
    cmd = _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW, realized="777")
    common = dict(
        snapshots=PostgresSnapshotRepository(pool),
        cash=cash,
        nav_repo=nav_repo,
        calendar=_BITGET,
        fx=FakeFxRateSource(),
        pool=pool,
    )

    results = await asyncio.gather(*(compute_daily_nav(cmd, **common) for _ in range(8)))

    assert all(r.source_hash == results[0].source_hash for r in results)
    assert all(r.closing_nav == Decimal("777") for r in results)
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM pos_nav_daily WHERE account_id = $1 AND nav_date = $2",
            account_id,
            results[0].nav_date,
        )
    assert count == 1


async def test_concurrent_different_valid_computations_one_wins_one_rejected(pool):
    """서로 다른(각자 self-consistent한) 두 계산이 같은 날짜를 놓고 진짜
    동시 커넥션으로 경합하면, 정확히 하나만 저장되고 나머지는 어댑터의
    `source_hash` 불일치 경로(`AdapterNavChainBrokenError`)로 거부된다 —
    순차 호출로는 관찰할 수 없는 실제 UNIQUE 제약 레이스다."""
    tenant_id, account_id = await _setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)
    common = dict(
        snapshots=PostgresSnapshotRepository(pool),
        cash=cash,
        nav_repo=nav_repo,
        calendar=_BITGET,
        fx=FakeFxRateSource(),
        pool=pool,
    )
    # 둘 다 opening(0)+realized+funding = closing(cash 1000 + mv 0)를 만족하는
    # self-consistent한 계산이지만, (realized, funding) 조합이 달라 source_hash가
    # 다르다.
    cmd_a = _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW, realized="1000")
    cmd_b = _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW, realized="500", funding="500")

    results = await asyncio.gather(
        compute_daily_nav(cmd_a, **common),
        compute_daily_nav(cmd_b, **common),
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, NAVSnapshot)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1, f"정확히 하나만 저장돼야 합니다: {results}"
    assert len(failures) == 1
    assert isinstance(failures[0], AdapterNavChainBrokenError)

    async with pool.acquire() as conn:
        stored = await nav_repo.get(conn, account_id, successes[0].nav_date)
        count = await conn.fetchval(
            "SELECT count(*) FROM pos_nav_daily WHERE account_id = $1 AND nav_date = $2",
            account_id,
            successes[0].nav_date,
        )
    assert stored is not None
    assert stored.source_hash == successes[0].source_hash
    assert count == 1


# ---------- 수치 성능 단언 — 순차 DB 왕복 수 상한(LB-18과 동일 기법) ----------


class _CountingAcquireContext:
    def __init__(self, inner: Any, queries: list[str]) -> None:
        self._inner = inner
        self._queries = queries
        self._conn: asyncpg.Connection | None = None

    async def __aenter__(self) -> asyncpg.Connection:
        self._conn = await self._inner.__aenter__()
        self._conn.add_query_logger(self._log)
        return self._conn

    def _log(self, record: object) -> None:
        self._queries.append(str(getattr(record, "query", "")))

    async def __aexit__(self, *exc_info: object) -> object:
        assert self._conn is not None
        self._conn.remove_query_logger(self._log)
        return await self._inner.__aexit__(*exc_info)


class _CountingPool:
    """실제 `pool`을 감싸 `compute_daily_nav` 한 회 호출이 소비하는 순차 DB
    왕복 수를 센다(`test_perf_journal_append.py`/LB-18과 동일 기법)."""

    def __init__(self, pool: asyncpg.Pool, queries: list[str]) -> None:
        self._pool = pool
        self._queries = queries

    def acquire(self) -> _CountingAcquireContext:
        return _CountingAcquireContext(self._pool.acquire(), self._queries)


_PERF_SAMPLE_COUNT = 30
_MAX_SEQUENTIAL_ROUND_TRIPS = 3  # list_open + nav_repo.get(prev) + nav_repo.insert


@pytest.mark.perf
async def test_compute_daily_nav_sequential_round_trips_and_latency(pool):
    """수치 성능 단언(D3) — `compute_daily_nav` 1회가 쓰는 순차 DB 왕복 수를
    직접 세어 구조 회귀를 막는다. 절대 지연은 실행환경(네트워크/디스크)에
    선형 비례해 흔들리므로(task-822/1059 decision과 동일 이유) 게이트로 쓰지
    않고 참고용으로만 print한다 — 왕복 수 상한만 차단 게이트로 남긴다."""
    tenant_id, account_id = await _setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)

    queries: list[str] = []
    await compute_daily_nav(
        _cmd(tenant_id=tenant_id, account_id=account_id, at=_NOW),
        snapshots=PostgresSnapshotRepository(pool),
        cash=cash,
        nav_repo=nav_repo,
        calendar=_BITGET,
        fx=FakeFxRateSource(),
        pool=_CountingPool(pool, queries),
    )
    round_trip_count = len(queries)

    latencies_ms: list[float] = []
    for _ in range(_PERF_SAMPLE_COUNT):
        tenant_i, account_i = await _setup_account(pool)
        cash_i = FakeCashSource()
        cash_i.seed(account_i, Decimal("1000"))
        started = time.perf_counter()
        await compute_daily_nav(
            _cmd(tenant_id=tenant_i, account_id=account_i, at=_NOW),
            snapshots=PostgresSnapshotRepository(pool),
            cash=cash_i,
            nav_repo=nav_repo,
            calendar=_BITGET,
            fx=FakeFxRateSource(),
            pool=pool,
        )
        latencies_ms.append((time.perf_counter() - started) * 1000)

    latencies_ms.sort()
    p95_ms = latencies_ms[int(len(latencies_ms) * 0.95)]
    print(
        f"\ncompute_daily_nav sequential DB round trips={round_trip_count} "
        f"(max={_MAX_SEQUENTIAL_ROUND_TRIPS}); "
        f"latency p95={p95_ms:.3f}ms (n={_PERF_SAMPLE_COUNT})"
    )

    assert round_trip_count <= _MAX_SEQUENTIAL_ROUND_TRIPS, (
        f"compute_daily_nav 순차 DB 왕복 수({round_trip_count})가 상한"
        f"({_MAX_SEQUENTIAL_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )


# ---------- 게이트 적색 재현(D3) — verify_chain 호출을 지우면 negative test가 뒤집히는가 ----------
#
# `tests/integration/foundation/market_data/test_replay_candles.py`
# (task-2972/LA-17 DEEPEN) 선례와 동일 기법이다: 자식 pytest 프로세스 안에서만
# 소스 문자열 치환으로 `compute_daily_nav`의 `nav.verify_chain(...)` 호출을
# 지워, 기존 `test_chain_break_when_rollforward_does_not_reconcile_is_rejected`
# 가 green(1 passed)에서 red(1 failed)로 뒤집히는지 실측한다(프로덕션 소스는
# 그대로 — 같은 인터프리터 프로세스에는 이 변형이 전혀 보이지 않는다).

_THIS_TESTFILE = "tests/integration/foundation/positions/test_compute_daily_nav.py"

_VERIFY_CHAIN_GUARD = (
    "    nav.verify_chain(\n"
    "        prev_nav if prev_nav is not None else "
    "_genesis(cmd.account_id, nav_date, cmd.base_currency),\n"
    "        candidate,\n"
    "    )\n"
)
_VERIFY_CHAIN_MUTATED = ""


def _source_mutation_plugin_source(module_name: str, guard: str, mutated: str) -> str:
    return f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module({module_name!r})
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {guard!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {mutated!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def _run_pytest_node(
    target_test: str, *, plugin_name: str | None = None, plugin_dir: Path | None = None
) -> subprocess.CompletedProcess[str]:
    repo_root = str(Path.cwd())
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target_test]
    env = dict(os.environ, PYTHONPATH=repo_root, PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")
    if plugin_name is not None:
        assert plugin_dir is not None
        command = [*command[:-1], "-p", plugin_name, command[-1]]
        env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{plugin_dir}"
    return subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
        check=False,
    )


def test_pytest_gate_turns_red_when_verify_chain_call_is_removed(tmp_path: Path) -> None:
    """게이트 적색 재현 — LB-15의 핵심 계약(체인 등식 위반을 저장 전에
    거부한다, task-714 DoD)을 지우면(verify_chain 호출 제거),
    `test_chain_break_when_rollforward_does_not_reconcile_is_rejected`가
    green에서 red로 뒤집혀야 한다 — 이 negative test가 실제로 그 회귀를
    잡는다는 증명(I-10)."""
    module = importlib.import_module("src.foundation.positions.application.compute_daily_nav")
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert source.count(_VERIFY_CHAIN_GUARD) == 1

    target_test = (
        f"{_THIS_TESTFILE}::test_chain_break_when_rollforward_does_not_reconcile_is_rejected"
    )
    baseline = _run_pytest_node(target_test)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin_name = "_mutate_compute_daily_nav_verify_chain"
    plugin_path = tmp_path / f"{plugin_name}.py"
    plugin_path.write_text(
        _source_mutation_plugin_source(
            "src.foundation.positions.application.compute_daily_nav",
            _VERIFY_CHAIN_GUARD,
            _VERIFY_CHAIN_MUTATED,
        ),
        encoding="utf-8",
    )

    mutated = _run_pytest_node(target_test, plugin_name=plugin_name, plugin_dir=tmp_path)
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout
