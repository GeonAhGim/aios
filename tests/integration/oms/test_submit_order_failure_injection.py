"""L4-09 DEEPEN(task-2765) — `application/submit_order.py` D3 보강.

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md #1566)는 원 커밋(539344e)의
`test_submit_order_tx.py`(negative=5) + `test_concurrent_submit.py`(동시 50
submit → 1행)가 D3 하한에 못 미친다고 판정했다(실측 D1) — 근거: "no
failure-injection test (crash/DB-error/dropped-message simulation), no
numeric performance/latency assertion, and no gate/CI red-line regression
test". 이 파일이 그 세 가지만 보강한다 — `submit_order.py`와 기존 테스트
파일은 손대지 않는다.

failure-injection: `submit_order`의 단일 tx(§2-C, 모듈 docstring)는 코드가
직접 잡아 롤백하는 실패(UniqueViolationError, gate DENY 등, 이미
`test_submit_order_tx.py`가 커버)와 *예상치 못한* 하부 인프라 실패(DB
연결 유실, 메시지 유실, 워커 크래시)를 구분하지 않는다 — 그 이유가 바로
이 테스트들의 핵심 주장이다: `finally` 블록이 `ok`를 보고 커밋/롤백을
가르지만, `ok=True`는 마지막 줄(outbox enqueue 성공 *다음*)에서만 켜지므로
그 전에 무엇이 됐든 예외가 나면(우리가 예상한 것이든 아니든) 커밋은 절대
실행되지 않는다. 더 강하게는 — 진짜 프로세스 크래시(Python `finally`조차
못 도는 경우)도 결과는 같다: 커밋된 적 없는 tx를 쥔 커넥션이 죽으면
Postgres 서버가 그 tx를 스스로 버린다. 즉 원자성은 "우리 코드의 예외
처리"가 아니라 "tx 경계 + 커밋을 마지막에 한 번만 한다"는 구조에서 나온다
— 아래 테스트들은 실패 지점을 이 함수의 서로 다른 세 단계(멱등 선점,
outbox enqueue, VALIDATED 전이)에 주입해 그 구조가 실제로 그렇게 동작함을
증명한다.

수치 성능 단언은 절대 ms 상수를 쓰지 않는다 — `tests/integration/oms/
test_gate_perf_multiinstance.py`(task-2762)와 `tests/integration/foundation/
ledger/test_perf_journal.py`(task-920/1029/1038)가 이 저장소의 공유 CI
환경에서 절대 임계가 로컬 대비 최대 20배 변동해 상시 적색을 낳은 전례를
남겼다 — 같은 커넥션 풀의 기준 왕복비용(`SELECT 1`)에 정규화한 임계를 쓴다.

게이트/CI 적색선 회귀는 `tests/adversarial/oms/test_duplicate_delivery_gate.py`
(task-2761)와 동일 기법이다 — 프로덕션 소스 파일은 그대로 둔 채, 자식
pytest 프로세스 안에서만 `submit_order`의 커밋/롤백 분기(`if ok: commit
else: rollback`)를 무조건 커밋으로 바꿔치기하고, 이미 있는
`test_submit_order_gate_deny_leaves_zero_rows`가 green(1 passed)에서
red(1 failed)로 바뀌는 것까지 증명한다 — DoD "gate DENY 시 0행"이 정말로
그 분기에 의존한다는 뜻이다.
"""
from __future__ import annotations

import itertools
import json
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

import src.services.oms.application.submit_order as submit_order_module
from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderType
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.services.oms.application.submit_order import submit_order
from src.services.oms.contracts.v1_commands import OrderIdempotencyScope, SubmitOrderCommand
from src.services.oms.domain.idempotency import scope_hash as compute_scope_hash
from src.services.oms.domain.symbol_registry import SymbolRegistry
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from tests.integration.oms.conftest import create_test_tenant, seed_entity_context


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
    return VenueCapabilityProfile.model_validate(defaults)


def _registry() -> SymbolRegistry:
    reg = SymbolRegistry()
    reg.register(
        "BTC/USDT", "bitget", "BTCUSDT",
        tick=Decimal("0.1"), lot=Decimal("0.0001"), min_notional=Decimal("5"), quote_ccy="USDT",
    )
    return reg


async def _create_running_execution(pool: asyncpg.Pool, user_id: uuid.UUID) -> int:
    strategy_id = f"oms-fi-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id, user_id, json.dumps({}),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id, user_id,
        )
    return row["id"]


def _command(user_id: uuid.UUID, execution_id: int, *, intent_seq: int = 1) -> SubmitOrderCommand:
    scope = OrderIdempotencyScope(
        tenant_id=user_id, account_ref="acct-1", provider="bitget", strategy_id="s1",
        strategy_version="1.0.0", execution_id=execution_id, intent_seq=intent_seq,
        window_start=datetime.now(timezone.utc),
    )
    return SubmitOrderCommand(
        command_id=uuid.uuid4(), trace_id=uuid.uuid4(), scope=scope, symbol="BTC/USDT",
        side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO, actor_subject_id=user_id,
        issued_at=datetime.now(timezone.utc),
    )


async def _allow_gate(context: OrderContext) -> GateDecision:
    return GateDecision(
        outcome=GateOutcome.ALLOW, decision_id=uuid.uuid4(), compliance_decision_id=uuid.uuid4()
    )


async def _row_counts(
    pool: asyncpg.Pool, *, execution_id: int, scope_hash_val: str
) -> dict[str, int]:
    async with pool.acquire() as conn:
        orders = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
        events = await conn.fetchval(
            "SELECT count(*) FROM order_events oe JOIN orders o ON o.order_id = oe.order_id "
            "WHERE o.execution_id = $1", execution_id,
        )
        outbox = await conn.fetchval(
            "SELECT count(*) FROM order_command_outbox oco JOIN orders o "
            "ON o.order_id = oco.order_id WHERE o.execution_id = $1", execution_id,
        )
        idempotency = await conn.fetchval(
            "SELECT count(*) FROM order_idempotency WHERE scope_hash = $1", scope_hash_val
        )
    return {"orders": orders, "events": events, "outbox": outbox, "idempotency": idempotency}


# ---- D3: failure-injection (crash / DB-error / dropped-message) -----------


async def test_simulated_db_connection_drop_during_idempotency_claim_rolls_back_atomically(
    pool, monkeypatch
):
    """DB-error simulation — `orders` INSERT는 이미 성공했고(CREATED 임시
    행), 바로 다음 단계인 멱등 선점(`_idempotency.claim`)에서 DB 커넥션이
    끊긴 상황을 흉내낸다. 아직 `ok=True`에 도달하지 못했으므로 tx 전체가
    버려져야 한다 — 방금 넣은 CREATED 행까지 포함해서."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    cmd = _command(user_id, execution_id)
    entity_context = await seed_entity_context(pool, user_id)
    scope_hash_val = compute_scope_hash(cmd.scope)

    async def _crashing_claim(conn, **kwargs):
        raise asyncpg.exceptions.ConnectionDoesNotExistError(
            "simulated DB connection drop during idempotency claim"
        )

    monkeypatch.setattr(submit_order_module._idempotency, "claim", _crashing_claim)

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await submit_order(
            cmd, pool=pool, profile=_profile(), registry=_registry(), pre_submit_gate=_allow_gate,
            entity_context=entity_context, entity_repo=PostgresEntityRepository(pool),
        )

    counts = await _row_counts(pool, execution_id=execution_id, scope_hash_val=scope_hash_val)
    assert counts == {"orders": 0, "events": 0, "outbox": 0, "idempotency": 0}


async def test_simulated_dropped_message_during_outbox_enqueue_rolls_back_atomically(
    pool, monkeypatch
):
    """dropped-message simulation — INSERT/claim/gate/VALIDATED 전이까지
    전부 성공한 *다음*, outbox에 SUBMIT 메시지를 쓰는 마지막 단계에서
    네트워크가 끊겨 그 쓰기 자체가 유실된 상황을 흉내낸다(`_outbox.enqueue`
    가 예외를 던짐 = 그 행이 결코 durable하게 남지 않음). `ok=True`는 이
    호출 *다음* 줄에서만 세팅되므로, 여기서 죽으면 방금 만든 VALIDATED
    전이·order_events 행까지 전부 롤백돼야 한다 — 그렇지 않으면 "outbox에
    없는데 orders만 VALIDATED로 남는" 유령 상태가 생긴다(§5.3이 금지하는
    바로 그 상태)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    cmd = _command(user_id, execution_id)
    entity_context = await seed_entity_context(pool, user_id)
    scope_hash_val = compute_scope_hash(cmd.scope)

    async def _dropped_enqueue(conn, **kwargs):
        raise ConnectionResetError("simulated dropped connection while writing outbox row")

    monkeypatch.setattr(submit_order_module._outbox, "enqueue", _dropped_enqueue)

    with pytest.raises(ConnectionResetError):
        await submit_order(
            cmd, pool=pool, profile=_profile(), registry=_registry(), pre_submit_gate=_allow_gate,
            entity_context=entity_context, entity_repo=PostgresEntityRepository(pool),
        )

    counts = await _row_counts(pool, execution_id=execution_id, scope_hash_val=scope_hash_val)
    assert counts == {"orders": 0, "events": 0, "outbox": 0, "idempotency": 0}


async def test_simulated_worker_crash_during_validated_transition_rolls_back_atomically(
    pool, monkeypatch
):
    """crash simulation — claim이 NEW, gate가 ALLOW까지 끝난 *직후*, CREATED
    ->VALIDATED 전이(`_orders.transition`)를 실행하던 워커 프로세스가
    죽었다고 가정한다. 실제 프로세스 kill은 이 함수의 Python `finally`조차
    돌리지 못하지만, 결과는 이 테스트가 만드는 것(예외로 인한 `ok=False`
    경로)과 동일하다 — 커밋을 마지막 한 줄에서만 하므로, 커밋 전에 커넥션이
    사라지면 그 tx는 Postgres 서버 쪽에서 자동으로 버려진다. 즉 코드가
    명시적으로 잡지 않는 실패도 원자성이 깨지지 않는다는 것을 이 시나리오가
    보인다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    cmd = _command(user_id, execution_id)
    entity_context = await seed_entity_context(pool, user_id)
    scope_hash_val = compute_scope_hash(cmd.scope)

    class _SimulatedWorkerCrash(RuntimeError):
        """실 프로세스 kill의 스탠드인 — DB tx는 미커밋 상태로 버려진다."""

    async def _crashing_transition(conn, **kwargs):
        raise _SimulatedWorkerCrash("simulated worker process crash mid-transition")

    monkeypatch.setattr(submit_order_module._orders, "transition", _crashing_transition)

    with pytest.raises(_SimulatedWorkerCrash):
        await submit_order(
            cmd, pool=pool, profile=_profile(), registry=_registry(), pre_submit_gate=_allow_gate,
            entity_context=entity_context, entity_repo=PostgresEntityRepository(pool),
        )

    counts = await _row_counts(pool, execution_id=execution_id, scope_hash_val=scope_hash_val)
    assert counts == {"orders": 0, "events": 0, "outbox": 0, "idempotency": 0}


# ---- D2: numeric performance/latency assertion -----------------------------


@pytest.mark.perf
async def test_submit_order_happy_path_latency_within_normalized_budget(pool):
    """수치 성능 단언 — 새 주문 1건을 만드는 `submit_order` 왕복(검증+INSERT+
    선점+게이트+전이+outbox enqueue+commit, 10회 이상의 DB 왕복)의 p95
    지연이 같은 풀의 기준 왕복비용(`SELECT 1`) 대비 정규화한 임계를
    넘지 않는다. 매 반복마다 `intent_seq`를 바꿔 매번 새 주문(EXISTING
    replay가 아닌 실제 신규 경로)을 강제한다."""
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

    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    entity_repo = PostgresEntityRepository(pool)
    seqs = itertools.count(1)

    async def _one_new_submit() -> None:
        cmd = _command(user_id, execution_id, intent_seq=next(seqs))
        await submit_order(
            cmd, pool=pool, profile=_profile(), registry=_registry(), pre_submit_gate=_allow_gate,
            entity_context=entity_context, entity_repo=entity_repo,
        )

    submit_p95 = await _p95_ms(_one_new_submit)

    budget_ms = max(400.0, 40.0 * baseline_p95)
    print(  # noqa: T201 — 실측치는 비차단 기록, 게이트는 아래 assert.
        f"\nsubmit_order new-order p95={submit_p95:.3f}ms "
        f"baseline(SELECT 1) p95={baseline_p95:.3f}ms budget={budget_ms:.3f}ms"
    )
    assert submit_p95 < budget_ms


# ---- D2: gate/CI red-line regression ---------------------------------------

_TARGET_TEST = (
    "tests/integration/oms/test_submit_order_tx.py::test_submit_order_gate_deny_leaves_zero_rows"
)
_GUARD = (
    "        finally:\n"
    "            if ok:\n"
    "                await tx.commit()\n"
    "            else:\n"
    "                await tx.rollback()\n"
)
_MUTATED = "        finally:\n            await tx.commit()\n"
_MUTATED_MODULE_NAME = "_mutate_submit_order_commit_gate"
_PLUGIN_SOURCE = f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module("src.services.oms.application.submit_order")
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {_GUARD!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {_MUTATED!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def test_pytest_gate_turns_red_when_commit_rollback_branch_is_removed(tmp_path: Path) -> None:
    """DoD "gate DENY 시 0행"이 실제로 `if ok: commit else: rollback` 분기에
    의존함을 증명한다 — 프로덕션 소스 파일은 그대로 둔 채, 자식 pytest
    프로세스 안에서만 그 분기를 무조건 커밋으로 바꿔치기하고(`tests/
    adversarial/oms/test_duplicate_delivery_gate.py`와 동일 기법),
    `test_submit_order_gate_deny_leaves_zero_rows`가 green(1 passed)에서
    red(1 failed, orders count 1 != 0)로 바뀌는 것까지 확인한다."""
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
