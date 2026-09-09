"""L4-15 DEEPEN(task-2761) — 중복 전달 흡수 D2 보강.

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md #1553)는 원 커밋(46f350e)의
`test_duplicate_delivery_storm.py`(3워커/1000회 동시성 흡수 증명, F9)에
수치 성능/처리량 단언과 게이트/CI 적색선 회귀 테스트가 없어 D1로 판정했다
(D3 하한 미달). 이 파일은 그 두 가지만 보강한다 — storm 테스트 자체와
`InboxProcessor`/`InboxRepository` 소스는 손대지 않는다.

D2(수치 처리량): 같은 물리 커넥션에서 순차 재전송되는 중복 이벤트(예: WS
재접속 재전송 폭주)를 흡수하는 왕복 수는 정확히 4(BEGIN + `INSERT ... ON
CONFLICT DO NOTHING` + COMMIT + pool 반환 시 RESET, 실측치)여야 한다 —
`_process_row`가 다시 실행되면(가드가 무력화되면) 왕복 수가 늘어난다. 왕복
수를 정확히 세려면 물리 커넥션이 하나여야 하므로(tests/performance/oms/
conftest.py와 동일 이유) 이 파일 전용 max_size=1 풀을 쓴다. 절대 처리량
(absorbed/s)은 공유 CI 환경의 DB 왕복 편차에 좌우되는 신호라 게이트로
쓰지 않는다(esc-826/task-1038/1521 decision과 동일) — print로만 남긴다.

D2(게이트/CI 적색선): `InboxRepository.insert_if_absent`의 `ON CONFLICT
(venue, provider_event_id) DO NOTHING`(§6 F9 inbox 멱등 규칙)이 이 리프의
유일한 동시성/중복 흡수 관문이다 — `tests/unit/exchanges/common/
test_rate_limiter.py::test_pytest_gate_turns_red_when_timeout_check_is_removed`
와 동일한 기법으로, 프로덕션 소스 파일은 그대로 둔 채 자식 pytest
프로세스 안에서 그 가드 문자열만 제거한 모듈 객체로 바꿔치기하고,
`test_inbox_processor.py::test_ingest_duplicate_event_absorbed_only_one_fill_row`
가 green(1 passed)에서 red(1 failed, `UniqueViolationError` — 테이블
UNIQUE 제약 자체는 살아있으므로 예외로 드러난다)로 바뀌는 것까지 증명한다.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from src.data.models.trading import OrderSide
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.contracts.v1_events import FillEvent, ProviderOrderEvent
from tests.integration.oms.conftest import create_test_user

# ---- D2: 수치 성능/처리량 단언 (CI 게이트) ---------------------------------

_DUP_REDELIVERY_N = 300
_DUP_ROUND_TRIPS_PER_CALL = 4  # BEGIN+INSERT(ON CONFLICT)+COMMIT+RESET, 실측치


def _dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def single_conn_pool() -> asyncpg.Pool:
    """왕복 수를 정확히 세려면 재전송 전체가 같은 물리 커넥션을 거쳐야
    한다(tests/performance/oms/conftest.py 모듈 docstring과 동일 이유)."""
    p = await asyncpg.create_pool(_dsn(), min_size=1, max_size=1)
    yield p
    await p.close()


async def _attach_round_trip_logger(pool: asyncpg.Pool) -> list[str]:
    queries: list[str] = []

    def _log(record: object) -> None:
        queries.append(getattr(record, "query", ""))

    conn = await pool.acquire()
    conn.add_query_logger(_log)
    await pool.release(conn)
    return queries


async def _seed_order(pool: asyncpg.Pool) -> tuple[str, str]:
    user_id = await create_test_user(pool)
    client_order_id = f"cid-{uuid4().hex}"
    exchange_order_id = f"ex-gate-{uuid4().hex}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO orders (
                user_id, client_order_id, exchange_order_id, strategy_id, strategy_version,
                symbol, exchange, side, order_type, quantity, status,
                filled_quantity, is_liquidation, asset_class
            ) VALUES ($1,$2,$3,'oms-gate-test','1.0.0','BTC/USDT','bitget','BUY',
                      'MARKET',$4,'SUBMITTED',0,false,'CRYPTO')
            """,
            user_id, client_order_id, exchange_order_id, Decimal("1"),
        )
    return client_order_id, exchange_order_id


def _full_fill_event(*, client_order_id: str, exchange_order_id: str) -> ProviderOrderEvent:
    fill_id = f"gate-fill-{uuid4().hex}"
    now = datetime.now(timezone.utc)
    fill = FillEvent(
        provider_fill_id=fill_id, venue="bitget", order_id=None,
        exchange_order_id=exchange_order_id, symbol="BTC/USDT", side=OrderSide.BUY,
        quantity=Decimal("1"), price=Decimal("100"), fee=Decimal("0"), fee_currency="USDT",
        liquidity="TAKER", venue_ts=now,
    )
    return ProviderOrderEvent(
        provider_event_id=fill_id, venue="bitget", venue_symbol="BTCUSDT",
        exchange_order_id=exchange_order_id, client_order_id=client_order_id,
        venue_status="FILLED", filled_quantity=Decimal("1"), average_price=Decimal("100"),
        last_fill=fill, venue_ts=now, received_at=now, source="WS",
        raw_hash=hashlib.sha256(fill_id.encode()).hexdigest(),
    )


@pytest.mark.perf
async def test_sequential_redelivery_absorption_round_trips_and_throughput_budget(
    single_conn_pool: asyncpg.Pool,
) -> None:
    pool = single_conn_pool
    client_order_id, exchange_order_id = await _seed_order(pool)
    ev = _full_fill_event(client_order_id=client_order_id, exchange_order_id=exchange_order_id)
    processor = InboxProcessor(pool)
    assert await processor.ingest(ev) is True  # 승자 — 워밍업, 예산 밖

    queries = await _attach_round_trip_logger(pool)
    queries.clear()

    started = time.perf_counter()
    results = [await processor.ingest(ev) for _ in range(_DUP_REDELIVERY_N)]
    elapsed_sec = time.perf_counter() - started

    achieved_per_sec = _DUP_REDELIVERY_N / elapsed_sec if elapsed_sec > 0 else float("inf")
    expected_round_trips = _DUP_REDELIVERY_N * _DUP_ROUND_TRIPS_PER_CALL
    print(
        f"\nsequential redelivery absorption: {_DUP_REDELIVERY_N} 재전송 {elapsed_sec:.3f}s에 "
        f"흡수 = {achieved_per_sec:.1f} absorbed/s(비차단 — task-1038/1521 decision); "
        f"순차 DB 왕복 수={len(queries)}(예산=={expected_round_trips})"
    )
    assert all(r is False for r in results)  # 전량 흡수 — F9
    assert len(queries) == expected_round_trips, (
        f"중복 흡수 경로 순차 DB 왕복 수({len(queries)})가 예산({expected_round_trips})과 "
        "다릅니다 — 흡수 경로가 더 이상 insert_if_absent 한 번에서 끝나지 않는다는 뜻이므로"
        "(예: 가드가 무력화돼 _process_row가 재실행됨) 회귀입니다."
    )


# ---- D2: 게이트/CI 적색선 회귀 테스트 --------------------------------------

_TARGET_TEST = (
    "tests/integration/oms/test_inbox_processor.py"
    "::test_ingest_duplicate_event_absorbed_only_one_fill_row"
)
_GUARD = '            "ON CONFLICT (venue, provider_event_id) DO NOTHING RETURNING id",'
_MUTATED_MODULE_NAME = "_mutate_inbox_dedup_guard"
_PLUGIN_SOURCE = f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module("src.services.oms.adapters.inbox_repository")
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {_GUARD!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, '            "RETURNING id",')
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def test_pytest_gate_turns_red_when_inbox_dedup_guard_is_removed(tmp_path: Path) -> None:
    """실제 중복 흡수 테스트가 통과하는 걸 먼저 확인하고, `ON CONFLICT DO
    NOTHING` 가드를 무력화하면(=흡수 대신 재삽입 시도) pytest가 exit 1로
    red가 되는 것까지 증명한다(gate/CI red-line regression proof,
    DEPTH_L4_BR task-1553 D2 미달 사유 해소)."""
    command = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", _TARGET_TEST,
    ]
    repo_root = str(Path.cwd())
    env = dict(
        os.environ, PYTHONPATH=repo_root, PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8"
    )
    baseline = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=120, check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin = tmp_path / f"{_MUTATED_MODULE_NAME}.py"
    plugin.write_text(_PLUGIN_SOURCE, encoding="utf-8")
    mutated_env = dict(env, PYTHONPATH=f"{repo_root}{os.pathsep}{tmp_path}")

    mutated = subprocess.run(
        [*command[:-1], "-p", _MUTATED_MODULE_NAME, command[-1]],
        capture_output=True, encoding="utf-8", errors="replace",
        env=mutated_env, timeout=120, check=False,
    )
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout
