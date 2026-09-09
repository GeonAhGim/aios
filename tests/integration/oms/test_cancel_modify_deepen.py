"""task-2768 DEEPEN of task-1603(L4-17, 9e7c57c) — `application/{cancel_order,
modify_order}.py` D3 보강.

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md #1603)는 원 커밋을 D1로
판정했다(negative=8 rejection matrix는 있으나 D3 하한 미달) — 근거: "No
failure-injection test, no performance assertion, no gate/CI red-line test,
and no concurrency/multi-instance proof (entirely single-threaded)". 이
파일이 그 네 가지만 보강한다 — `cancel_order.py`/`modify_order.py`와 기존
`test_cancel_order.py`/`test_modify_order.py`는 손대지 않는다.

failure-injection: 두 함수 모두 `submit_order.py`(L4-09, task-2765 DEEPEN이
이미 이 기법을 적용)와 같은 단일 tx 골격(잠금→전이→outbox enqueue, `ok`가
마지막 줄에서만 켜짐)이다. 아래 각 함수마다 2단계(전이 자체, outbox
enqueue 직전)에 실패를 주입해 어느 지점에서 죽어도 tx 전체가 롤백됨을
증명한다.

수치 성능 단언은 `test_submit_order_failure_injection.py`(task-2765)와
동일하게 절대 ms가 아니라 같은 풀의 `SELECT 1` p95에 정규화한 임계를 쓴다
(공유 CI 환경의 절대 임계 변동 전례, 같은 파일 docstring 참조).

게이트/CI 적색선 회귀는 같은 파일이 방금 추가한 outbox-enqueue 실패주입
테스트 자신을 표적으로 삼는다 — `cancel_order`/`modify_order`의
`if ok: commit else: rollback` 분기를 자식 pytest 프로세스 안에서만
무조건 커밋으로 바꿔치기하면, 그 테스트가 검증하는 "전이는 성공했지만
enqueue가 실패하면 전이도 롤백된다"는 주장이 green→red로 뒤집힌다 — 즉 그
주장이 실제로 그 분기에 의존한다는 뜻이다(`test_submit_order_failure_
injection.py`의 `test_pytest_gate_turns_red_when_commit_rollback_branch_is_
removed`와 동일 기법).

동시성/다중 인스턴스 증명 — 기존 테스트는 전부 단일 호출 단일 await였다
(DEPTH 지적 "entirely single-threaded"). 두 테스트를 추가한다: (1) 같은
주문에 5개의 독립 커넥션(서로 다른 OMS 인스턴스를 흉내)이 동시에
`cancel_order`를 호출해도 `orders.version`이 순서대로만 증가하고
손실갱신·중복 enqueue가 없음을 증명한다(자기루프라 전부 성공해야 한다).
(2) `cancel_order`(인스턴스 A)와 거래소 만료를 반영하는 직접 `repo.
transition`(VENUE_EXPIRED, 인스턴스 B — 예: reconciler)이 같은 주문을 놓고
동시에 경합하는 상황을 재현한다 — `get_for_update`의 행 잠금이 둘을
직렬화하므로 어느 쪽이 먼저 커밋하든 최종 상태는 항상 일관된다: B는
어떤 순서로도 성공하고(자기루프는 상태를 안 바꾸므로), A는 B보다 먼저
커밋하면 성공하고 나중이면 이미 터미널(EXPIRED)이 되어 깨끗하게
`InvalidOrderTransitionError`로 거부된다 — 절대 두 인스턴스가 서로 다른
최종 상태를 "동시에 맞다"고 믿는 상황(오손 상태)이 나오지 않는다.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

import src.services.oms.application.cancel_order as cancel_order_module
import src.services.oms.application.modify_order as modify_order_module
from src.data.models.base import AssetClass
from src.data.models.trading import OrderStatus, OrderType
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.application.cancel_order import cancel_order
from src.services.oms.application.modify_order import modify_order
from src.services.oms.contracts.v1_commands import CancelOrderCommand, ModifyOrderCommand
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from src.services.oms.domain.errors import InvalidOrderTransitionError
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile
from tests.integration.oms.conftest import create_test_user


def _profile(**overrides: object) -> VenueCapabilityProfile:
    defaults: dict[str, object] = {
        "venue": "bitget",
        "asset_classes": [AssetClass.CRYPTO],
        "order_types": {OrderType.MARKET, OrderType.LIMIT},
        "time_in_force": {"GTC", "IOC"},
        "supports_client_order_id": True,
        "client_order_id_max_len": 40,
        "client_order_id_charset": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "id_policy": "STABLE",
        "supports_modify": True,
        "supports_cancel": "YES",
        "supports_ws_orders": True,
        "supports_batch": False,
        "price_tick": {},
        "qty_lot": {},
        "min_notional": {},
        "rate_limits": {},
        "submit_timeout": TimeoutBudget(),
        "query_timeout": TimeoutBudget(),
        "market_hours": None,
        "max_open_orders_per_symbol": 20,
        "verified": "DOC_ONLY",
    }
    defaults.update(overrides)
    return VenueCapabilityProfile(**defaults)  # type: ignore[arg-type]


async def _insert_order(
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
    *,
    status: str = "ACKNOWLEDGED",
    order_type: str = "MARKET",
    quantity: Decimal = Decimal("1"),
    filled_quantity: Decimal = Decimal("0"),
) -> uuid.UUID:
    price = Decimal("100") if order_type == "LIMIT" else None
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, price, status, filled_quantity,
                exchange_order_id
            ) VALUES ($1, $2, 'oms-deepen-test', '1.0.0', 'BTC/USDT', 'bitget', 'BUY',
                      $3, $4, $5, $6, $7, 'EX-1')
            RETURNING order_id
            """,
            user_id,
            f"oms-deepen-{uuid.uuid4().hex}",
            order_type,
            quantity,
            price,
            status,
            filled_quantity,
        )


def _cancel_command(
    order_id: uuid.UUID, tenant_id: uuid.UUID, **overrides: object
) -> CancelOrderCommand:
    defaults: dict[str, object] = {
        "command_id": uuid.uuid4(),
        "trace_id": uuid.uuid4(),
        "order_id": order_id,
        "tenant_id": tenant_id,
        "reason": "TEST_CANCEL",
        "actor_subject_id": tenant_id,
        "issued_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return CancelOrderCommand(**defaults)  # type: ignore[arg-type]


def _modify_command(
    order_id: uuid.UUID, tenant_id: uuid.UUID, **overrides: object
) -> ModifyOrderCommand:
    defaults: dict[str, object] = {
        "command_id": uuid.uuid4(),
        "trace_id": uuid.uuid4(),
        "order_id": order_id,
        "tenant_id": tenant_id,
        "reason": "TEST_MODIFY",
        "actor_subject_id": tenant_id,
        "issued_at": datetime.now(timezone.utc),
        "new_price": Decimal("110"),
        "new_quantity": None,
    }
    defaults.update(overrides)
    return ModifyOrderCommand(**defaults)  # type: ignore[arg-type]


async def _row_counts(pool: asyncpg.Pool, order_id: uuid.UUID, event: str) -> dict[str, int]:
    async with pool.acquire() as conn:
        version = await conn.fetchval("SELECT version FROM orders WHERE order_id = $1", order_id)
        events = await conn.fetchval(
            "SELECT count(*) FROM order_events WHERE order_id = $1 AND event = $2",
            order_id, event,
        )
        outbox = await conn.fetchval(
            "SELECT count(*) FROM order_command_outbox WHERE order_id = $1", order_id
        )
    return {"version": version, "events": events, "outbox": outbox}


# ---- D3: failure-injection (crash / DB-error / dropped-message) -----------


async def test_simulated_db_error_during_cancel_transition_rolls_back_atomically(
    pool, monkeypatch
):
    """DB-error simulation — `cancel_order`의 유일한 쓰기 단계(`_orders.
    transition`, order_events INSERT + orders UPDATE)에서 커넥션이 끊겼다고
    가정한다. `ok=True`는 이 호출 *다음* 줄에서만 세팅되므로 여기서 죽으면
    아무 것도 커밋되지 않아야 한다."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED")
    cmd = _cancel_command(order_id, user_id)

    async def _crashing_transition(conn, **kwargs):
        raise asyncpg.exceptions.ConnectionDoesNotExistError(
            "simulated DB connection drop during CANCEL_REQUESTED transition"
        )

    monkeypatch.setattr(cancel_order_module._orders, "transition", _crashing_transition)

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await cancel_order(cmd, pool=pool)

    counts = await _row_counts(pool, order_id, "CANCEL_REQUESTED")
    assert counts == {"version": 0, "events": 0, "outbox": 0}


async def test_simulated_dropped_message_during_cancel_outbox_enqueue_rolls_back_atomically(
    pool, monkeypatch
):
    """dropped-message simulation — 잠금+전이(order_events INSERT + orders
    UPDATE, version 0->1)까지는 tx 안에서 이미 실행된 *다음*, outbox에
    CANCEL 메시지를 쓰는 마지막 단계에서 네트워크가 끊긴 상황을 흉내낸다.
    `ok=True`는 이 호출 다음 줄에서만 세팅되므로, 이미 실행된 전이까지
    통째로 롤백되어야 한다 — 그렇지 않으면 "outbox에 없는데 orders/
    order_events만 갱신된" 유령 상태가 생긴다."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED")
    cmd = _cancel_command(order_id, user_id)

    async def _dropped_enqueue(conn, **kwargs):
        raise ConnectionResetError("simulated dropped connection while writing outbox row")

    monkeypatch.setattr(cancel_order_module._outbox, "enqueue", _dropped_enqueue)

    with pytest.raises(ConnectionResetError):
        await cancel_order(cmd, pool=pool)

    counts = await _row_counts(pool, order_id, "CANCEL_REQUESTED")
    assert counts == {"version": 0, "events": 0, "outbox": 0}


async def test_simulated_worker_crash_during_modify_transition_rolls_back_atomically(
    pool, monkeypatch
):
    """crash simulation — capability 사전거부(§DoD, tx 밖)는 이미 통과했고,
    `modify_order`의 유일한 쓰기 단계(`_orders.transition`)를 실행하던
    워커가 죽었다고 가정한다. 실 프로세스 kill과 결과가 같다(모듈 docstring
    §submit_order.py 동일 논거 — 커밋 전에 커넥션이 사라지면 Postgres가
    tx를 스스로 버린다)."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED", order_type="LIMIT")
    cmd = _modify_command(order_id, user_id)

    class _SimulatedWorkerCrash(RuntimeError):
        """실 프로세스 kill의 스탠드인."""

    async def _crashing_transition(conn, **kwargs):
        raise _SimulatedWorkerCrash("simulated worker process crash mid-transition")

    monkeypatch.setattr(modify_order_module._orders, "transition", _crashing_transition)

    with pytest.raises(_SimulatedWorkerCrash):
        await modify_order(cmd, pool=pool, profile=_profile())

    counts = await _row_counts(pool, order_id, "MODIFY_REQUESTED")
    assert counts == {"version": 0, "events": 0, "outbox": 0}


async def test_simulated_dropped_message_during_modify_outbox_enqueue_rolls_back_atomically(
    pool, monkeypatch
):
    """dropped-message simulation — `cancel_order`의 동일 테스트와 대칭.
    MODIFY_REQUESTED 자기루프 전이까지 tx 안에서 이미 실행된 *다음*, outbox
    MODIFY 메시지를 쓰는 마지막 단계에서 유실이 일어나면 전이까지 롤백돼야
    한다."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED", order_type="LIMIT")
    cmd = _modify_command(order_id, user_id)

    async def _dropped_enqueue(conn, **kwargs):
        raise ConnectionResetError("simulated dropped connection while writing outbox row")

    monkeypatch.setattr(modify_order_module._outbox, "enqueue", _dropped_enqueue)

    with pytest.raises(ConnectionResetError):
        await modify_order(cmd, pool=pool, profile=_profile())

    counts = await _row_counts(pool, order_id, "MODIFY_REQUESTED")
    assert counts == {"version": 0, "events": 0, "outbox": 0}


# ---- D2: numeric performance/latency assertion -----------------------------


async def _p95_ms(reps: int, step) -> float:
    samples = []
    for _ in range(reps):
        t0 = time.perf_counter()
        await step()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    return samples[int(len(samples) * 0.95) - 1]


@pytest.mark.perf
async def test_cancel_order_ack_self_loop_latency_within_normalized_budget(pool):
    """수치 성능 단언 — CANCEL_REQUESTED 자기루프(잠금+전이+outbox enqueue+
    commit)의 p95 지연이 같은 풀의 기준 왕복비용(`SELECT 1`) 대비 정규화한
    임계를 넘지 않는다. 자기루프라 같은 주문에 반복 적용해도 매번 유효하다
    (`submit_order.py`의 신규 idempotency scope 제약과 달리 재사용 가능)."""
    reps = 15
    async with pool.acquire() as conn:
        baseline_p95 = await _p95_ms(reps, lambda: conn.fetchval("SELECT 1"))

    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED")

    async def _one_cancel() -> None:
        await cancel_order(_cancel_command(order_id, user_id), pool=pool)

    cancel_p95 = await _p95_ms(reps, _one_cancel)

    budget_ms = max(400.0, 40.0 * baseline_p95)
    print(  # noqa: T201 — 실측치는 비차단 기록, 게이트는 아래 assert.
        f"\ncancel_order ACK self-loop p95={cancel_p95:.3f}ms "
        f"baseline(SELECT 1) p95={baseline_p95:.3f}ms budget={budget_ms:.3f}ms"
    )
    assert cancel_p95 < budget_ms


@pytest.mark.perf
async def test_modify_order_ack_limit_self_loop_latency_within_normalized_budget(pool):
    """수치 성능 단언 — `cancel_order`와 대칭. MODIFY_REQUESTED 자기루프의
    p95 지연을 같은 풀의 `SELECT 1` p95에 정규화해 단언한다."""
    reps = 15
    async with pool.acquire() as conn:
        baseline_p95 = await _p95_ms(reps, lambda: conn.fetchval("SELECT 1"))

    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED", order_type="LIMIT")
    profile = _profile()

    async def _one_modify() -> None:
        await modify_order(_modify_command(order_id, user_id), pool=pool, profile=profile)

    modify_p95 = await _p95_ms(reps, _one_modify)

    budget_ms = max(400.0, 40.0 * baseline_p95)
    print(  # noqa: T201 — 실측치는 비차단 기록, 게이트는 아래 assert.
        f"\nmodify_order ACK/LIMIT self-loop p95={modify_p95:.3f}ms "
        f"baseline(SELECT 1) p95={baseline_p95:.3f}ms budget={budget_ms:.3f}ms"
    )
    assert modify_p95 < budget_ms


# ---- D2: gate/CI red-line regression ---------------------------------------

_GUARD = (
    "        finally:\n"
    "            if ok:\n"
    "                await tx.commit()\n"
    "            else:\n"
    "                await tx.rollback()\n"
)
_MUTATED = "        finally:\n            await tx.commit()\n"


def _plugin_source(module_import_path: str) -> str:
    return f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module({module_import_path!r})
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {_GUARD!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {_MUTATED!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def _assert_commit_rollback_gate_flips_red(
    tmp_path: Path, module_import_path: str, target_test: str, plugin_module_name: str
) -> None:
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target_test]
    repo_root = str(Path.cwd())
    env = dict(os.environ, PYTHONPATH=repo_root, PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")

    baseline = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=120, check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin = tmp_path / f"{plugin_module_name}.py"
    plugin.write_text(_plugin_source(module_import_path), encoding="utf-8")
    mutated_env = dict(env, PYTHONPATH=f"{repo_root}{os.pathsep}{tmp_path}")

    mutated = subprocess.run(
        [*command[:-1], "-p", plugin_module_name, command[-1]],
        capture_output=True, encoding="utf-8", errors="replace",
        env=mutated_env, timeout=120, check=False,
    )
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout


def test_pytest_gate_turns_red_when_cancel_commit_rollback_branch_is_removed(
    tmp_path: Path,
) -> None:
    """`cancel_order`의 dropped-message 실패주입 테스트가 실제로 `if ok:
    commit else: rollback` 분기에 의존함을 증명한다 — 프로덕션 소스는 그대로
    둔 채, 자식 pytest 프로세스 안에서만 그 분기를 무조건 커밋으로
    바꿔치기하고, 이 파일의 `test_simulated_dropped_message_during_cancel_
    outbox_enqueue_rolls_back_atomically`가 green(1 passed)에서
    red(1 failed, version 1 != 0)로 바뀌는 것까지 확인한다."""
    _assert_commit_rollback_gate_flips_red(
        tmp_path,
        "src.services.oms.application.cancel_order",
        "tests/integration/oms/test_cancel_modify_deepen.py::"
        "test_simulated_dropped_message_during_cancel_outbox_enqueue_rolls_back_atomically",
        "_mutate_cancel_order_commit_gate",
    )


def test_pytest_gate_turns_red_when_modify_commit_rollback_branch_is_removed(
    tmp_path: Path,
) -> None:
    """`test_pytest_gate_turns_red_when_cancel_commit_rollback_branch_is_
    removed`와 대칭 — `modify_order` 쪽 동일 분기·동일 실패주입 테스트로
    같은 증명을 반복한다."""
    _assert_commit_rollback_gate_flips_red(
        tmp_path,
        "src.services.oms.application.modify_order",
        "tests/integration/oms/test_cancel_modify_deepen.py::"
        "test_simulated_dropped_message_during_modify_outbox_enqueue_rolls_back_atomically",
        "_mutate_modify_order_commit_gate",
    )


# ---- D3: concurrency / multi-instance proof --------------------------------


async def test_concurrent_cancel_from_multiple_instances_all_succeed_without_lost_updates(
    pool,
):
    """DoD 보강 — 5개의 독립 커넥션(서로 다른 OMS 인스턴스/재시도를 흉내)이
    같은 ACKNOWLEDGED 주문에 동시에 `cancel_order`를 호출한다.
    `get_for_update`의 행 잠금이 직렬화하고, CANCEL_REQUESTED는 자기루프라
    매번 유효하므로 전부 성공해야 한다 — 손실갱신(lost update) 없이
    `version`이 정확히 인스턴스 수만큼만 증가하고, order_events/outbox도
    정확히 그 수만큼만 남는다(중복·누락 없음)."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED")
    workers = 5

    async def _attempt() -> OrderStatus:
        result = await cancel_order(_cancel_command(order_id, user_id), pool=pool)
        return result.status

    results = await asyncio.gather(*(_attempt() for _ in range(workers)), return_exceptions=True)
    failures = [r for r in results if isinstance(r, Exception)]
    assert failures == [], f"자기루프인데 실패가 발생했습니다: {failures}"
    assert all(r == OrderStatus.ACKNOWLEDGED for r in results)

    counts = await _row_counts(pool, order_id, "CANCEL_REQUESTED")
    assert counts == {"version": workers, "events": workers, "outbox": workers}


async def test_concurrent_cancel_races_with_venue_expiry_never_corrupts_state(pool):
    """DoD 보강 — 인스턴스 A(`cancel_order`)와 인스턴스 B(reconciler가
    거래소 만료를 반영하는 직접 `PostgresOrderRepository.transition`,
    VENUE_EXPIRED)가 같은 ACKNOWLEDGED 주문을 놓고 동시에 경합한다. 둘 다
    실행 직전에 `get_for_update`로 최신 상태를 다시 읽으므로(§4.2), 어느
    쪽이 먼저 잠금을 얻어 커밋하든 최종 상태는 항상 일관된다:
    - B(만료)는 어느 순서로도 성공한다 — A의 자기루프는 상태를 EXPIRED로
      바꾸지 않으므로 B가 보는 출발 상태는 항상 ACKNOWLEDGED다.
    - A(취소)는 B보다 먼저 커밋하면 성공하고(자기루프), B보다 나중이면
      이미 터미널(EXPIRED)이 된 것을 보고 `InvalidOrderTransitionError`로
      깨끗이 거부된다(추가 쓰기 0건).
    두 결과 모두 `order_events`/outbox 행 수가 실제 성공 건수와 정확히
    일치해야 한다 — 그렇지 않으면 두 인스턴스가 서로 다른 최종 상태를
    "동시에 맞다"고 믿는 오손 상태다."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED")
    repo = PostgresOrderRepository()

    async def _cancel_attempt() -> OrderStatus:
        result = await cancel_order(_cancel_command(order_id, user_id), pool=pool)
        return result.status

    async def _expire_attempt() -> OrderStatus:
        async with pool.acquire() as conn:
            tx = conn.transaction()
            await tx.start()
            ok = False
            try:
                current = await repo.get_for_update(conn, order_id)
                event = OrderTransitionEvent(
                    order_id=order_id,
                    from_status=current.status,
                    to_status=OrderStatus.EXPIRED,
                    event="VENUE_EXPIRED",
                    reason_code=None,
                    actor_subject_id="system",
                    trace_id=uuid.uuid4(),
                    command_id=None,
                    provider_event_id="venue-expiry-sim",
                    occurred_at=datetime.now(timezone.utc),
                    payload_hash="e" * 64,
                )
                result = await repo.transition(
                    conn,
                    order_id=order_id,
                    expected_status=current.status,
                    expected_version=current.version,
                    new_status=OrderStatus.EXPIRED,
                    patch={},
                    event=event,
                )
                ok = True
            finally:
                if ok:
                    await tx.commit()
                else:
                    await tx.rollback()
        return result.status

    cancel_result, expire_result = await asyncio.gather(
        _cancel_attempt(), _expire_attempt(), return_exceptions=True
    )

    assert not isinstance(expire_result, Exception), (
        f"만료 반영은 경합 순서와 무관하게 항상 성공해야 합니다: {expire_result}"
    )
    assert expire_result == OrderStatus.EXPIRED

    async with pool.acquire() as conn:
        final_status = await conn.fetchval(
            "SELECT status FROM orders WHERE order_id = $1", order_id
        )
    assert final_status == OrderStatus.EXPIRED.value, "터미널 상태가 되돌려지면 안 됩니다."

    if isinstance(cancel_result, Exception):
        assert isinstance(cancel_result, InvalidOrderTransitionError), (
            f"유일하게 허용된 실패 모드가 아닙니다: {cancel_result!r}"
        )
        expected_events, expected_outbox = 1, 0
    else:
        assert cancel_result == OrderStatus.ACKNOWLEDGED
        expected_events, expected_outbox = 2, 1

    async with pool.acquire() as conn:
        event_count = await conn.fetchval(
            "SELECT count(*) FROM order_events WHERE order_id = $1", order_id
        )
        outbox_count = await conn.fetchval(
            "SELECT count(*) FROM order_command_outbox WHERE order_id = $1", order_id
        )
    assert event_count == expected_events
    assert outbox_count == expected_outbox
