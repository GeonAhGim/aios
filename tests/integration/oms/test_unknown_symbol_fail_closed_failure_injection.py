"""L4-04 DEEPEN(task-2805) — `domain/symbol_registry.py`/`venue_profile.py`
fail-closed gate D3 보강.

DEPTH 감사(task-2722, `docs/audit/DEPTH_L4_BR.md` #74)는 원 커밋(a809a6c7)의
`test_venue_profile_registry.py`(negative>=4, adversarial
`test_require_verified_rejects_unverified_spec_adversarial`,
`test_unknown_symbol_fail_closed.py`)가 D3 하한에 못 미친다고 판정했다(실측
D1) — 근거: "No failure-injection test (DB error/network failure/crash) and
no numeric latency/throughput assertion; no multi-instance/adversarial-bypass
proof against the fail-closed gate itself". 이 파일이 그 세 가지만 보강한다 —
`symbol_registry.py`/`venue_profile.py`/`submit_order.py`와 기존 테스트
파일은 손대지 않는다(`test_submit_order_failure_injection.py`/task-2765,
`test_unknown_resolver_failure_injection.py`/task-2769와 동일 관례).

failure-injection: `submit_order()`의 fail-closed 게이트 자체
(`registry.to_venue(cmd.symbol, profile.venue)`)는 순수 함수라 DB/네트워크
I/O가 없다 — 그래서 "DB 오류/네트워크 장애/크래시"를 이 게이트에 주입한다는
것은 이 정확한 호출 지점이 (가상의 원격 심볼 메타데이터 조회처럼) 그런
이유로 실패하더라도 원자성이 깨지지 않는다는 것을 증명한다는 뜻이다 —
`submit_order.py`의 단일 tx는 `pool.acquire()` *이후*에 열리고 이 게이트
호출은 그보다 앞이므로(§2-C), 이 지점에서 무엇이 되었든 예외가 나면 tx 자체가
아예 시작되지 않는다. 세 테스트는 같은 호출 지점에 서로 다른 예외 종류(DB
연결 유실/네트워크 드롭/워커 크래시)를 주입해 `UnknownSymbolError`가 아닌
다른 예외로도 동일하게 0행(orders/outbox/adapter 호출)이 보장됨을 보인다 —
즉 이 불변은 "우리가 예측한 예외 처리"가 아니라 "그 호출이 tx 시작보다
앞에 있다"는 구조에서 나온다.

수치 성능 단언은 `test_submit_order_failure_injection.py`(task-2765)와
동일하게 절대 ms 상수를 쓰지 않고 같은 풀의 `SELECT 1` p95에 정규화한다.

multi-instance/adversarial-bypass 증명: (1) 동시에 여러 인스턴스가 같은
미등록 심볼로 제출을 시도해도 전부 독립적으로 fail-closed되고 어느 하나도
행을 남기지 않는다 — 레지스트리 조회에 락이 없다는 사실(순수 dict 조회,
동시 read만 발생)이 실제로 안전함을 증명한다. (2) 등록된 캐노니컬
"BTC/USDT"(bitget)를 흉내내려는 변형 입력(대소문자, 공백, venue 교차,
부분/초과 문자열, 유니코드 동형이의 문자)이 전부 정확 일치 실패로
거부되는지 — 즉 `to_venue`가 조용히 정규화해 우회를 허용하지 않는지를
증명한다.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderType
from src.exchanges.bitget.venue_profile import BITGET_SPOT_PROFILE
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.application.submit_order import submit_order
from src.services.oms.application.wiring import (
    build_outbox_dispatcher,
    build_production_symbol_registry,
)
from src.services.oms.contracts.v1_commands import OrderIdempotencyScope, SubmitOrderCommand
from src.services.oms.domain.errors import UnknownSymbolError
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from tests.integration.oms.conftest import create_test_tenant, seed_entity_context
from tests.support.oms_outbox_fakes import ScriptedAdapter


async def _allow_gate(context: OrderContext) -> GateDecision:
    return GateDecision(
        outcome=GateOutcome.ALLOW, decision_id=uuid.uuid4(), compliance_decision_id=uuid.uuid4()
    )


async def _drain_stale_outbox(pool: asyncpg.Pool) -> None:
    """공유 테스트 DB에는 트랜잭션 격리가 없다(`test_unknown_symbol_fail_closed.py`와
    동일 이유) — 우리 단언 전에 잔여 PENDING 행을 비운다."""

    async def _resolve(tenant_id: uuid.UUID, exchange: str) -> ScriptedAdapter:
        return ScriptedAdapter()

    drainer = build_outbox_dispatcher(
        pool,
        resolve_adapter=_resolve,
        outbox_repo=OutboxRepository(),
        order_repo=PostgresOrderRepository(),
        worker_id=f"drain-stale-outbox-{uuid.uuid4().hex[:8]}",
    )
    for _ in range(20):
        report = await drainer.dispatch_once()
        if report.claimed == 0:
            return


async def _create_running_execution(pool: asyncpg.Pool, user_id: uuid.UUID) -> int:
    strategy_id = f"oms-symreg-fi-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', '{}'::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id, user_id,
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


def _command(
    user_id: uuid.UUID, execution_id: int, *, symbol: str, intent_seq: int
) -> SubmitOrderCommand:
    scope = OrderIdempotencyScope(
        tenant_id=user_id, account_ref="acct-1", provider="bitget", strategy_id="s1",
        strategy_version="1.0.0", execution_id=execution_id, intent_seq=intent_seq,
        window_start=datetime.now(timezone.utc),
    )
    return SubmitOrderCommand(
        command_id=uuid.uuid4(), trace_id=uuid.uuid4(), scope=scope, symbol=symbol,
        side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO, actor_subject_id=user_id,
        issued_at=datetime.now(timezone.utc),
    )


async def _row_counts(pool: asyncpg.Pool, *, execution_id: int) -> dict[str, int]:
    async with pool.acquire() as conn:
        orders = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
        outbox = await conn.fetchval(
            "SELECT count(*) FROM order_command_outbox oco JOIN orders o "
            "ON o.order_id = oco.order_id WHERE o.execution_id = $1", execution_id,
        )
    return {"orders": orders, "outbox": outbox}


async def _assert_zero_footprint_and_zero_adapter_calls(
    pool: asyncpg.Pool, *, execution_id: int
) -> None:
    counts = await _row_counts(pool, execution_id=execution_id)
    assert counts == {"orders": 0, "outbox": 0}

    spy = ScriptedAdapter()

    async def _resolve_adapter(tenant_id: uuid.UUID, exchange: str) -> ScriptedAdapter:
        return spy

    dispatcher = build_outbox_dispatcher(
        pool,
        resolve_adapter=_resolve_adapter,
        outbox_repo=OutboxRepository(),
        order_repo=PostgresOrderRepository(),
        worker_id=f"fail-closed-fi-test-{uuid.uuid4().hex[:8]}",
    )
    report = await dispatcher.dispatch_once()
    assert report.claimed == 0
    assert spy.calls == []


# ---- D3: failure-injection (DB error / network failure / crash) against ----
# ---- the fail-closed gate itself -------------------------------------------


async def test_db_error_during_gate_lookup_still_fails_closed_zero_rows(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB-error simulation — `registry.to_venue`(fail-closed 게이트)가 (가상의
    원격 조회 실패로) `asyncpg.ConnectionDoesNotExistError`를 던진다고
    가정한다. 이 호출은 `submit_order`의 tx가 열리기 *전*이므로 어떤 행도
    남으면 안 된다."""
    await _drain_stale_outbox(pool)
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    cmd = _command(user_id, execution_id, symbol="BTC/USDT", intent_seq=1)

    registry = build_production_symbol_registry()

    def _crashing_to_venue(canonical: str, venue: str) -> str:
        raise asyncpg.exceptions.ConnectionDoesNotExistError(
            "simulated DB connection drop during symbol registry lookup"
        )

    monkeypatch.setattr(registry, "to_venue", _crashing_to_venue)

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await submit_order(
            cmd, pool=pool, profile=BITGET_SPOT_PROFILE, registry=registry,
            pre_submit_gate=_allow_gate, entity_context=entity_context,
            entity_repo=PostgresEntityRepository(pool),
        )

    await _assert_zero_footprint_and_zero_adapter_calls(pool, execution_id=execution_id)


async def test_network_failure_during_gate_lookup_still_fails_closed_zero_rows(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """network-failure simulation — 같은 호출 지점에 `ConnectionResetError`를
    주입한다(예: 향후 원격 심볼 메타데이터 서비스로 옮겨졌을 때의 드롭)."""
    await _drain_stale_outbox(pool)
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    cmd = _command(user_id, execution_id, symbol="BTC/USDT", intent_seq=1)

    registry = build_production_symbol_registry()

    def _dropped_to_venue(canonical: str, venue: str) -> str:
        raise ConnectionResetError("simulated network drop during symbol registry lookup")

    monkeypatch.setattr(registry, "to_venue", _dropped_to_venue)

    with pytest.raises(ConnectionResetError):
        await submit_order(
            cmd, pool=pool, profile=BITGET_SPOT_PROFILE, registry=registry,
            pre_submit_gate=_allow_gate, entity_context=entity_context,
            entity_repo=PostgresEntityRepository(pool),
        )

    await _assert_zero_footprint_and_zero_adapter_calls(pool, execution_id=execution_id)


async def test_worker_crash_during_gate_lookup_still_fails_closed_zero_rows(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """crash simulation — 게이트 호출 도중 워커 프로세스가 죽었다고 가정한다
    (`RuntimeError` 서브클래스로 표현). `UnknownSymbolError`가 아닌 완전히
    예상 밖의 예외로도 동일하게 0행이 보장됨을 증명한다 — 이 불변이 특정
    예외 타입을 잡는 코드가 아니라 tx 경계 구조에서 나온다는 뜻."""
    await _drain_stale_outbox(pool)
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    cmd = _command(user_id, execution_id, symbol="BTC/USDT", intent_seq=1)

    registry = build_production_symbol_registry()

    class _SimulatedWorkerCrash(RuntimeError):
        pass

    def _crashing_to_venue(canonical: str, venue: str) -> str:
        raise _SimulatedWorkerCrash("simulated worker crash mid symbol registry lookup")

    monkeypatch.setattr(registry, "to_venue", _crashing_to_venue)

    with pytest.raises(_SimulatedWorkerCrash):
        await submit_order(
            cmd, pool=pool, profile=BITGET_SPOT_PROFILE, registry=registry,
            pre_submit_gate=_allow_gate, entity_context=entity_context,
            entity_repo=PostgresEntityRepository(pool),
        )

    await _assert_zero_footprint_and_zero_adapter_calls(pool, execution_id=execution_id)


# ---- D2: numeric performance/latency assertion -----------------------------


@pytest.mark.perf
async def test_unknown_symbol_rejection_latency_within_normalized_budget(
    pool: asyncpg.Pool,
) -> None:
    """수치 성능 단언 — 미등록 심볼 제출이 게이트에서 즉시(DB INSERT/전이/
    outbox enqueue를 하나도 거치지 않고) 거부되는 왕복의 p95 지연이 같은
    풀의 기준 왕복비용(`SELECT 1`) 대비 정규화한 임계를 넘지 않는다."""
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
    registry = build_production_symbol_registry()
    seqs = iter(range(1, reps + 1))

    async def _one_rejection() -> None:
        cmd = _command(user_id, execution_id, symbol="ZZZUSDT", intent_seq=next(seqs))
        with pytest.raises(UnknownSymbolError):
            await submit_order(
                cmd, pool=pool, profile=BITGET_SPOT_PROFILE, registry=registry,
                pre_submit_gate=_allow_gate, entity_context=entity_context,
                entity_repo=entity_repo,
            )

    reject_p95 = await _p95_ms(_one_rejection)

    budget_ms = max(400.0, 40.0 * baseline_p95)
    print(  # noqa: T201 -- 실측치는 비차단 기록, 게이트는 아래 assert.
        f"\nunknown-symbol rejection p95={reject_p95:.3f}ms "
        f"baseline(SELECT 1) p95={baseline_p95:.3f}ms budget={budget_ms:.3f}ms"
    )
    assert reject_p95 < budget_ms


# ---- D3: multi-instance / adversarial-bypass proof against the gate -------


async def test_concurrent_unknown_symbol_submissions_all_fail_closed_zero_rows(
    pool: asyncpg.Pool,
) -> None:
    """multi-instance 증명 — 여러 인스턴스가 동시에 같은 미등록 심볼로
    제출을 시도해도 (`SymbolRegistry`가 락 없는 순수 dict 조회이므로) 서로
    간섭 없이 각자 독립적으로 `UnknownSymbolError`로 거부되고, 어느 하나도
    행을 남기지 않는다."""
    await _drain_stale_outbox(pool)
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    entity_repo = PostgresEntityRepository(pool)
    registry = build_production_symbol_registry()

    async def _one_attempt(intent_seq: int) -> BaseException | None:
        cmd = _command(user_id, execution_id, symbol="ZZZUSDT", intent_seq=intent_seq)
        try:
            await submit_order(
                cmd, pool=pool, profile=BITGET_SPOT_PROFILE, registry=registry,
                pre_submit_gate=_allow_gate, entity_context=entity_context,
                entity_repo=entity_repo,
            )
        except BaseException as exc:  # noqa: BLE001 -- we assert the type below.
            return exc
        return None

    results = await asyncio.gather(*(_one_attempt(seq) for seq in range(1, 11)))

    assert len(results) == 10
    assert all(isinstance(r, UnknownSymbolError) for r in results)
    await _assert_zero_footprint_and_zero_adapter_calls(pool, execution_id=execution_id)


@pytest.mark.parametrize(
    "variant_symbol",
    [
        "btc/usdt",  # case-fold bypass attempt
        " BTC/USDT",  # leading whitespace
        "BTC/USDT ",  # trailing whitespace
        "BTC/USD",  # substring (missing suffix)
        "BTC/USDTX",  # superstring (extra suffix)
        "BTCUSDT",  # already-venue-form used as canonical
        "BTC​/USDT",  # zero-width space injected
        "ВТC/USDT",  # Cyrillic homoglyph 'В' instead of Latin 'B'
    ],
)
async def test_adversarial_symbol_variants_of_registered_canonical_fail_closed_zero_rows(
    pool: asyncpg.Pool, variant_symbol: str
) -> None:
    """adversarial-bypass 증명 — "BTC/USDT"(bitget에 실제 등록됨)를 흉내내려는
    변형 입력이 전부 정확 일치 실패로 거부된다. `to_venue`가 대소문자
    정규화·트림·유사문자 치환 같은 암묵적 관용을 하지 않는다는 뜻이다 —
    그런 관용이 하나라도 있으면 그 변형은 등록되지 않은 채로도 통과해 버려,
    운영자가 의도하지 않은 심볼로 실주문이 나가는 우회 경로가 된다."""
    await _drain_stale_outbox(pool)
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    entity_repo = PostgresEntityRepository(pool)
    registry = build_production_symbol_registry()
    cmd = _command(user_id, execution_id, symbol=variant_symbol, intent_seq=1)

    with pytest.raises(UnknownSymbolError):
        await submit_order(
            cmd, pool=pool, profile=BITGET_SPOT_PROFILE, registry=registry,
            pre_submit_gate=_allow_gate, entity_context=entity_context,
            entity_repo=entity_repo,
        )

    await _assert_zero_footprint_and_zero_adapter_calls(pool, execution_id=execution_id)


async def test_registered_canonical_used_against_unregistered_venue_fails_closed(
    pool: asyncpg.Pool,
) -> None:
    """venue-cross-over 증명 — "005930.KS"는 kis/nh에는 등록돼 있지만 bitget
    프로파일(이 테스트가 쓰는 `BITGET_SPOT_PROFILE`)에는 등록돼 있지 않다.
    한 venue에 등록된 캐노니컬이 다른 venue에서 그대로 통과하면 안 된다
    (R8 — 심볼 등록은 venue별 독립)."""
    await _drain_stale_outbox(pool)
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    entity_repo = PostgresEntityRepository(pool)
    registry = build_production_symbol_registry()

    assert registry.to_venue("005930.KS", "kis") == "005930"

    cmd = _command(user_id, execution_id, symbol="005930.KS", intent_seq=1)

    with pytest.raises(UnknownSymbolError):
        await submit_order(
            cmd, pool=pool, profile=BITGET_SPOT_PROFILE, registry=registry,
            pre_submit_gate=_allow_gate, entity_context=entity_context,
            entity_repo=entity_repo,
        )

    await _assert_zero_footprint_and_zero_adapter_calls(pool, execution_id=execution_id)
