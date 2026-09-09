"""L4-16 DEEPEN(task-2769) — `application/unknown_resolver.py` D3 보강.

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md #1604)는 원 커밋(6e0c368)의
`test_unknown_resolver.py`(실DB 3종) + `test_unknown_resolver_limits.py`
(경계값 매트릭스 등 negative=5+)가 D3 하한에 못 미친다고 판정했다(실측
D1) — 근거: "no failure-injection test; no performance assertion; no
gate-red test; no concurrency test". 이 파일이 그 네 가지만 보강한다 —
`unknown_resolver.py`/`unknown_resolver_writes.py`와 기존 테스트 파일은
손대지 않는다(`tests/integration/oms/test_submit_order_failure_injection.py`
/task-2765와 동일 관례).

failure-injection: 세 개의 최종 커밋 분기(`apply_resolved_as`/
`apply_resolved_absent`/`escalate`, unknown_resolver_writes.py)는 각자
독립된 tx를 열고 마지막에만 commit한다(모듈 docstring). `repo.transition`
자체에서 예상치 못한 인프라 실패(DB 연결 유실/드롭된 메시지/워커 크래시)가
나면 그 tx는 커밋 전이므로 통째로 버려져야 한다 — 아래 세 테스트는 세
결과 분기 각각에 실패를 주입해 그 구조가 실제로 그렇게 동작함을 증명한다.
(resolve_unknown 자체의 최초 조회는 tx 밖의 단발 FOR UPDATE라 롤백 대상이
아니다 — 그래서 주입 지점은 항상 각 분기의 `repo.transition` 호출이다.)

수치 성능 단언은 `test_submit_order_failure_injection.py`(task-2765)와
동일하게 절대 ms 상수를 쓰지 않고 같은 풀의 `SELECT 1` p95에 정규화한다
(이 저장소 공유 CI 환경에서 절대 임계가 상시 적색이 된 전례, task-2762 등).

게이트/CI 적색선 회귀는 같은 파일의 서브프로세스 뮤테이션 기법을 재사용한다
— `resolve_unknown`의 fail-closed 3조건(§4.2 "still_open이면 ABSENT
금지") 중 `not still_open`을 무조건 `True`로 바꿔치기하면, 이미 있는
`test_still_open_blocks_absent_even_past_boundary`가 green에서 red로
바뀌어야 한다 — 그 가드가 실제로 그 테스트의 결과를 지탱한다는 뜻이다.

multi-instance/adversarial-bypass 증명: 같은 order_id에 대해 동시에 두
`resolve_unknown` 인스턴스를 돌린다. RESOLVED_AS/RESOLVED_ABSENT 분기는
`current.status is not UNKNOWN`이 상태를 실제로 바꾸므로 두 번째 인스턴스가
자기 tx 안에서 그 가드에 걸려 조용히 멱등 반환한다(중복 쓰기 없음) — 이를
증명한다. 반대로 `UNRESOLVED_LIMIT`(escalate)는 §4.2가 규정한 자기루프라
상태가 안 바뀌고, 그래서 그 가드는 두 번째 인스턴스를 막지 못한다 — 실제로
돌려 확인한 결과 두 인스턴스 모두 독립적으로 escalate까지 끝내
`order_events` 2행 + `safety_control` 2행이 생긴다. 이건 버그로 위장하지
않는다 — 대신 안전 불변(I-10 "우회불가")이 여전히 지켜지는지, 즉 이
경합에서 안전 통제가 유실되는 경로(0건)가 있는지를 확인하는 것이 진짜
질문이다. 아래 테스트는 실제 관측된 개수(정확히 2)를 그대로 단언하고,
그 중 하나라도 활성화되면 통과지 실패가 아니라는 이유를 docstring에
남긴다 — 중복 쓰기는 낭비지만 안전 통제가 사라지는 방향의 결함(escape)은
아니다.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

import src.services.oms.application.unknown_resolver as unknown_resolver_module
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.oms.application import unknown_resolver
from tests.integration.oms.conftest import create_test_user


async def _insert_unknown_order(
    pool: asyncpg.Pool, user_id: UUID, *, unknown_since: datetime
) -> tuple[UUID, str]:
    client_order_id = f"unk-fi-{uuid4().hex}"
    async with pool.acquire() as conn:
        order_id = await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, status, filled_quantity,
                is_liquidation, asset_class, unknown_since
            ) VALUES ($1,$2,'oms-unk-fi-test','1.0.0','BTC/USDT','bitget','BUY','MARKET',
                      1,'UNKNOWN',0,false,'CRYPTO',$3)
            RETURNING order_id
            """,
            user_id,
            client_order_id,
            unknown_since,
        )
    return order_id, client_order_id


async def _no_sleep(seconds: float) -> None:
    return None


def _found_order(client_order_id: str, *, exchange_order_id: str) -> Order:
    return Order(
        client_order_id=client_order_id,
        exchange_order_id=exchange_order_id,
        strategy_id="oms-unk-fi-test",
        strategy_version="1.0.0",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        status=OrderStatus.ACKNOWLEDGED,
        asset_class=AssetClass.CRYPTO,
    )


class _LookupAdapter:
    def __init__(
        self, *, results: list[Order | None], open_orders: list[Order] | None = None
    ) -> None:
        self._results = list(results)
        self._open_orders = open_orders or []
        self.lookup_calls = 0

    async def find_order_by_client_id(self, client_order_id: str) -> Order | None:
        idx = min(self.lookup_calls, len(self._results) - 1)
        self.lookup_calls += 1
        return self._results[idx]

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        return self._open_orders


class _CrashingAdapter:
    """네트워크 드롭 시뮬레이션 — 첫 조회 자체에서 예외를 던진다."""

    async def find_order_by_client_id(self, client_order_id: str) -> Order | None:
        raise ConnectionResetError("simulated network drop during provider lookup")

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        raise AssertionError("still_open 조회는 lookup 실패 이후 도달하면 안 된다")


async def _order_event_rows(pool: asyncpg.Pool, order_id: UUID) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT event FROM order_events WHERE order_id = $1 ORDER BY seq", order_id
        )


async def _order_status(pool: asyncpg.Pool, order_id: UUID) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT status FROM orders WHERE order_id = $1", order_id)


# ---- D3: failure-injection (network drop / DB-error / worker crash) -------


async def test_simulated_network_drop_during_lookup_leaves_order_untouched(pool: asyncpg.Pool):
    user_id = await create_test_user(pool)
    order_id, _ = await _insert_unknown_order(
        pool, user_id, unknown_since=datetime.now(timezone.utc)
    )

    with pytest.raises(ConnectionResetError):
        await unknown_resolver.resolve_unknown(
            order_id,
            adapter=_CrashingAdapter(),
            pool=pool,
            risk_gate_repo=PostgresRiskGateRepository(pool),
            clock=lambda: datetime.now(timezone.utc),
            sleep=_no_sleep,
        )

    assert await _order_status(pool, order_id) == "UNKNOWN"
    assert await _order_event_rows(pool, order_id) == []


async def test_simulated_db_connection_drop_during_resolved_as_transition_rolls_back(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
):
    """`RESOLVED_AS` 분기 — `repo.transition`(orders UPDATE+order_events
    INSERT를 감싸는 tx)이 DB 연결 유실로 죽으면, 아직 `ok=True`에 못
    도달했으므로 그 tx는 통째로 버려져야 한다."""
    user_id = await create_test_user(pool)
    order_id, client_order_id = await _insert_unknown_order(
        pool, user_id, unknown_since=datetime.now(timezone.utc)
    )
    found = _found_order(client_order_id, exchange_order_id="ex-fi-1")
    adapter = _LookupAdapter(results=[found])

    async def _crashing_transition(conn, **kwargs):
        raise asyncpg.exceptions.ConnectionDoesNotExistError(
            "simulated DB connection drop during RESOLVED_AS transition"
        )

    monkeypatch.setattr(unknown_resolver_module._orders, "transition", _crashing_transition)

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await unknown_resolver.resolve_unknown(
            order_id,
            adapter=adapter,
            pool=pool,
            risk_gate_repo=PostgresRiskGateRepository(pool),
            clock=lambda: datetime.now(timezone.utc),
            sleep=_no_sleep,
        )

    assert await _order_status(pool, order_id) == "UNKNOWN"
    assert await _order_event_rows(pool, order_id) == []


async def test_simulated_dropped_message_during_resolved_absent_transition_rolls_back(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
):
    """`RESOLVED_ABSENT` 분기 — outbound 쓰기가 유실된 상황(네트워크
    ConnectionResetError)을 흉내낸다. 커밋 전이므로 order는 여전히
    UNKNOWN이어야 하고(§5.3이 금지하는 "유령 FAILED" 없음), 재시도 시
    다시 이 경로를 탈 수 있어야 한다."""
    user_id = await create_test_user(pool)
    stale_since = datetime.now(timezone.utc) - timedelta(seconds=200)
    order_id, _client_order_id = await _insert_unknown_order(
        pool, user_id, unknown_since=stale_since
    )
    adapter = _LookupAdapter(results=[None, None], open_orders=[])

    async def _dropped_transition(conn, **kwargs):
        raise ConnectionResetError(
            "simulated dropped connection during RESOLVED_ABSENT transition"
        )

    monkeypatch.setattr(unknown_resolver_module._orders, "transition", _dropped_transition)

    with pytest.raises(ConnectionResetError):
        await unknown_resolver.resolve_unknown(
            order_id,
            adapter=adapter,
            pool=pool,
            risk_gate_repo=PostgresRiskGateRepository(pool),
            clock=lambda: datetime.now(timezone.utc),
            sleep=_no_sleep,
            max_attempts=2,
            backoff=(0.0, 0.0),
        )

    assert await _order_status(pool, order_id) == "UNKNOWN"
    assert await _order_event_rows(pool, order_id) == []


async def test_simulated_worker_crash_during_escalate_transition_rolls_back_and_skips_control(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
):
    """`UNRESOLVED_LIMIT`(escalate) 분기 — `repo.transition`이 (시뮬레이션한)
    워커 크래시로 죽으면, `should_activate`는 그 호출 *다음* 줄에서만
    True가 되므로 절대 True가 될 수 없다 — order도 UNKNOWN 그대로, 안전
    통제도 걸리지 않아야 한다(둘 다 안 걸리는 것이 맞다 — "주문 전이
    없이 안전 통제만 걸림" 같은 불일치 상태가 없다는 뜻)."""
    user_id = await create_test_user(pool)
    order_id, _client_order_id = await _insert_unknown_order(
        pool, user_id, unknown_since=datetime.now(timezone.utc)
    )
    adapter = _LookupAdapter(results=[None, None])
    risk_gate_repo = PostgresRiskGateRepository(pool)

    class _SimulatedWorkerCrash(RuntimeError):
        pass

    async def _crashing_transition(conn, **kwargs):
        raise _SimulatedWorkerCrash("simulated worker crash mid UNRESOLVED_LIMIT transition")

    monkeypatch.setattr(unknown_resolver_module._orders, "transition", _crashing_transition)

    with pytest.raises(_SimulatedWorkerCrash):
        await unknown_resolver.resolve_unknown(
            order_id,
            adapter=adapter,
            pool=pool,
            risk_gate_repo=risk_gate_repo,
            clock=lambda: datetime.now(timezone.utc),
            sleep=_no_sleep,
            max_attempts=2,
            backoff=(0.0, 0.0),
        )

    assert await _order_status(pool, order_id) == "UNKNOWN"
    assert await _order_event_rows(pool, order_id) == []
    controls = await risk_gate_repo.list_active_controls(tenant_id=user_id)
    assert len(controls) == 0


# ---- D2: numeric performance/latency assertion -----------------------------


@pytest.mark.perf
async def test_resolved_as_fast_path_latency_within_normalized_budget(pool: asyncpg.Pool):
    """수치 성능 단언 — 첫 조회에서 바로 찾는(RESOLVED_AS, 재시도 없음) 가장
    빠른 경로의 왕복(초기 조회 1 + lookup 1 + apply_resolved_as tx 내부
    get_for_update+events.append+conditional_update)이 같은 풀의 `SELECT 1`
    p95 대비 정규화한 임계를 넘지 않는다. 매 반복마다 새 UNKNOWN 주문을
    새로 심어 EXISTING-replay가 아닌 실제 RESOLVED_AS 경로를 강제한다."""
    reps = 15

    async def _p95_ms(step) -> float:
        samples = []
        for _ in range(reps):
            t0 = time.perf_counter()
            await step()
            samples.append((time.perf_counter() - t0) * 1000)
        samples.sort()
        return samples[int(len(samples) * 0.95) - 1]

    async with pool.acquire() as conn:
        baseline_p95 = await _p95_ms(lambda: conn.fetchval("SELECT 1"))

    user_id = await create_test_user(pool)
    risk_gate_repo = PostgresRiskGateRepository(pool)

    async def _one_resolved_as() -> None:
        order_id, client_order_id = await _insert_unknown_order(
            pool, user_id, unknown_since=datetime.now(timezone.utc)
        )
        found = _found_order(client_order_id, exchange_order_id=f"ex-perf-{uuid4().hex[:8]}")
        await unknown_resolver.resolve_unknown(
            order_id,
            adapter=_LookupAdapter(results=[found]),
            pool=pool,
            risk_gate_repo=risk_gate_repo,
            clock=lambda: datetime.now(timezone.utc),
            sleep=_no_sleep,
        )

    resolve_p95 = await _p95_ms(_one_resolved_as)

    budget_ms = max(400.0, 40.0 * baseline_p95)
    print(  # noqa: T201 — 실측치는 비차단 기록, 게이트는 아래 assert.
        f"\nresolve_unknown RESOLVED_AS p95={resolve_p95:.3f}ms "
        f"baseline(SELECT 1) p95={baseline_p95:.3f}ms budget={budget_ms:.3f}ms"
    )
    assert resolve_p95 < budget_ms


# ---- D2: gate/CI red-line regression ---------------------------------------

_TARGET_TEST = (
    "tests/adversarial/oms/test_unknown_resolver_limits.py::"
    "test_still_open_blocks_absent_even_past_boundary"
)
_GUARD = "            not still_open\n"
_MUTATED = "            True\n"
_MUTATED_MODULE_NAME = "_mutate_unknown_resolver_still_open_guard"
_PLUGIN_SOURCE = f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module("src.services.oms.application.unknown_resolver")
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {_GUARD!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {_MUTATED!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def test_pytest_gate_turns_red_when_still_open_guard_is_removed(tmp_path: Path) -> None:
    """DoD의 "미체결 목록에도 없음"이 실제로 `not still_open` 조건에
    의존함을 증명한다 — 프로덕션 소스 파일은 그대로 둔 채, 자식 pytest
    프로세스 안에서만 그 조건을 무조건 `True`로 바꿔치고(`test_submit_order_
    failure_injection.py`/task-2765와 동일 기법),
    `test_still_open_blocks_absent_even_past_boundary`가 green(1 passed)에서
    red(1 failed)로 바뀌는 것까지 확인한다."""
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", _TARGET_TEST]
    repo_root = str(Path.cwd())
    env = dict(os.environ, PYTHONPATH=repo_root, PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")

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


# ---- D3: multi-instance / adversarial-bypass proof -------------------------


async def test_concurrent_resolvers_resolved_as_race_is_idempotent_no_duplicate_writes(
    pool: asyncpg.Pool,
):
    """두 워커가 동시에 같은 UNKNOWN 주문을 RESOLVED_AS로 해소하려 경합한다.
    `apply_resolved_as`의 `current.status is not UNKNOWN` 가드는 상태가
    실제로 바뀌므로(UNKNOWN->ACKNOWLEDGED) 두 번째 인스턴스가 자기 tx의
    fresh get_for_update에서 그 가드에 걸려 조용히 멱등 반환해야 한다 —
    order_events는 정확히 1행이어야 한다(중복 없음)."""
    user_id = await create_test_user(pool)
    order_id, client_order_id = await _insert_unknown_order(
        pool, user_id, unknown_since=datetime.now(timezone.utc)
    )
    found = _found_order(client_order_id, exchange_order_id="ex-race-1")
    risk_gate_repo = PostgresRiskGateRepository(pool)

    async def _one_resolver() -> OrderStatus:
        result = await unknown_resolver.resolve_unknown(
            order_id,
            adapter=_LookupAdapter(results=[found]),
            pool=pool,
            risk_gate_repo=risk_gate_repo,
            clock=lambda: datetime.now(timezone.utc),
            sleep=_no_sleep,
        )
        return result.status

    statuses = await asyncio.gather(_one_resolver(), _one_resolver())

    assert all(s is OrderStatus.ACKNOWLEDGED for s in statuses)
    events = await _order_event_rows(pool, order_id)
    assert [e["event"] for e in events] == ["RESOLVED_AS"]


async def test_concurrent_resolvers_escalate_race_never_loses_the_safety_control(
    pool: asyncpg.Pool,
):
    """같은 경합을 `UNRESOLVED_LIMIT`(escalate) 경로에서 돌린다. 이 이벤트는
    §4.2가 규정한 자기루프(UNKNOWN->UNKNOWN)라 `current.status is not
    UNKNOWN` 가드가 두 번째 인스턴스를 막지 못한다 — 실측 결과 둘 다
    독립적으로 escalate까지 끝내 order_events 2행 + safety_control 2행이
    생긴다(중복 쓰기, 낭비지만 버그로 위장하지 않는다). 이 테스트가 실제로
    지키는 안전 불변(I-10)은 "적어도 하나는 ACTIVE" — 경합 때문에 통제가
    0건이 되는(안전장치가 조용히 사라지는) 경로가 없다는 것이다."""
    user_id = await create_test_user(pool)
    order_id, _client_order_id = await _insert_unknown_order(
        pool, user_id, unknown_since=datetime.now(timezone.utc)
    )
    risk_gate_repo = PostgresRiskGateRepository(pool)

    async def _one_resolver() -> OrderStatus:
        result = await unknown_resolver.resolve_unknown(
            order_id,
            adapter=_LookupAdapter(results=[None, None]),
            pool=pool,
            risk_gate_repo=risk_gate_repo,
            clock=lambda: datetime.now(timezone.utc),
            sleep=_no_sleep,
            max_attempts=2,
            backoff=(0.0, 0.0),
        )
        return result.status

    statuses = await asyncio.gather(_one_resolver(), _one_resolver())

    assert all(s is OrderStatus.UNKNOWN for s in statuses)
    events = await _order_event_rows(pool, order_id)
    assert [e["event"] for e in events] == ["UNRESOLVED_LIMIT", "UNRESOLVED_LIMIT"]

    controls = await risk_gate_repo.list_active_controls(tenant_id=user_id)
    assert len(controls) >= 1  # 안전 불변: 절대 0건이면 안 된다.
    assert len(controls) == 2  # 실측된 실제 동작(중복) — 문서화 목적.
    for control in controls:
        assert control.scope is SafetyScope.ACCOUNT
        assert control.scope_ref == str(user_id)
