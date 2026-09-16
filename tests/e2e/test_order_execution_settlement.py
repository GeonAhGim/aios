"""H-7a 백엔드 e2e #1 — 주문 제출 -> paper 체결 -> positions 정산, 풀스택.

Spec: docs/design/ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md H-7
("백엔드 e2e 3건: 주문->체결->정산 ..."). 성능 예산은
docs/design/ADR-2026-09-09-C-t2-depth-bar-and-spec-coverage.md의 축별 표
("주문 제출->ACK p95 50ms(paper)")를 이 e2e 경로 전체(사전거래 게이트 ->
멱등 claim -> 거래소 체결 -> positions 정산 -> 이벤트 발행)에 대해
정규화 배율로 단언한다.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9(주문 파이프라인),
docs/specs/L4_market_data_positions_ledger_v1.0.md(FA-* positions 정산 SSOT).

**어댑터 선택에 대한 기록**: `src/exchanges/paper/simulator_adapter.py`의
`PaperSimulatorAdapter`(L4-23)는 `place_order()`가 매번 새 `order_id`
(uuid4())로 `Order`를 재구성해 반환한다("venue는 OMS 내부 id를 모른다"
문서화된 설계) — 그런데 `submit_order()`/`submit_with_fence()`는 둘 다
`repository.update_from_exchange(conn, submitted, expected_status=...)`를
`submitted.order_id`로 호출해 claim 행을 찾는다. Bitget 실어댑터와
`FakeExchangeAdapter`는 둘 다 `order.model_copy(update=...)`로 입력
`order_id`를 보존해 이 계약을 지키지만, `PaperSimulatorAdapter`는 지키지
않는다 — 이 조합으로 실제 호출하면 매번 `ConcurrencyConflictError`(대상
row가 없음)로 죽는다(직접 실험으로 확인). `grep -rn "PaperSimulatorAdapter("
src/`가 프로덕션 어디에도 이 클래스를 실제로 배선한 곳이 없음을 보여준다 —
L4-23은 어댑터 자체(잔고·체결·수수료)만 격리 테스트됐고(`test_paper_
simulator_adapter.py`, `place_order()`를 직접 호출), OMS 제출 파이프라인
(`submit_order`)과의 통합은 이 시점까지 한 번도 실행된 적이 없는 별도
미완료 리프다. 이 파일은 그 통합 결함을 여기서 조용히 우회하지 않고,
이 저장소가 `submit_order()` 파이프라인의 "paper 어댑터"로 실제로 이미
쓰고 있는 대역(`is_paper_trading=True`인 `FakeExchangeAdapter` —
`tests/adversarial/order_service/test_kill_switch_blocks_execution_loop.py`
와 동일 관례)으로 정산까지의 전 구간을 검증한다. `PaperSimulatorAdapter`
자체의 잔고/체결 시뮬레이션은 `test_paper_simulator_adapter.py`가 이미
D2까지 커버한다 — 이 파일의 범위는 그 위에 얹히는 OMS 제출->정산 배선이다.

이 저장소에는 Redis 백엔드가 없다(ADR-2026-09-09-B M2-8 — 이벤트버스는
`InProcessEventBus`뿐, Phase2에서 Redis Streams 예정) — 이 leaf는 실
Postgres(TEST_DATABASE_URL)로 범위를 좁힌다.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import AccountBalance, Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.submit import submit_order
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.foundation.execution_ownership.conftest import create_execution


def _make_adapter(
    *, place_order_result_status: OrderStatus = OrderStatus.FILLED
) -> FakeExchangeAdapter:
    return FakeExchangeAdapter(
        exchange_name="paper_sim",
        is_paper_trading=True,
        place_order_result_status=place_order_result_status,
        closes=[Decimal("30000")] * 30,  # 체결가(시장가 fallback) 고정
        usdt_balance=AccountBalance(
            exchange="paper_sim", asset="USDT", total=Decimal("100000"), available=Decimal("100000")
        ),
    )


def _make_order(
    execution_id: int,
    client_order_id_label: str,
    *,
    order_type: OrderType = OrderType.MARKET,
    price: Decimal | None = None,
    quantity: Decimal = Decimal("1"),
) -> Order:
    # 이 테스트 DB는 트랜잭션 롤백이 아니라 uuid 접미사로 격리한다(레포
    # 전역 관례, docs/TESTING.md) — 재실행 시 이전 실행이 남긴 행과
    # client_order_id UNIQUE 충돌을 피한다.
    return Order(
        client_order_id=f"{client_order_id_label}-{uuid4().hex[:8]}",
        strategy_id="e2e-settlement",
        strategy_version="1.0.0",
        execution_id=execution_id,
        symbol="BTC/USDT",
        exchange="paper_sim",
        side=OrderSide.BUY,
        order_type=order_type,
        quantity=quantity,
        price=Money(amount=price, currency=Currency.USDT) if price is not None else None,
        status=OrderStatus.CREATED,
        asset_class=AssetClass.CRYPTO,
    )


async def test_order_submission_fills_and_settles_into_positions(pool: asyncpg.Pool) -> None:
    user_id = await create_test_tenant(pool)
    execution_id = await create_execution(pool, user_id)
    adapter = _make_adapter()
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    published: list[tuple[str, dict[str, object]]] = []

    async def publish(topic: str, payload: dict[str, object]) -> None:
        published.append((topic, payload))

    order = _make_order(execution_id, "settle-1")
    persisted = await submit_order(
        order, user_id=user_id, adapter=adapter, pool=pool, publish=publish, pre_submit_gate=gate
    )

    assert persisted.status is OrderStatus.FILLED
    assert persisted.filled_quantity == Decimal("1")

    async with pool.acquire() as conn:
        db_status = await conn.fetchval(
            "SELECT status FROM orders WHERE client_order_id = $1", order.client_order_id
        )
        position = await conn.fetchrow(
            "SELECT quantity, average_entry_price FROM positions WHERE execution_id = $1",
            execution_id,
        )
    assert db_status == "FILLED"
    assert position is not None
    assert position["quantity"] == Decimal("1")
    assert position["average_entry_price"] == Decimal("30000")

    assert published
    assert published[-1][0] == "order.status.changed"
    assert published[-1][1]["status"] == "FILLED"


async def test_exchange_rejection_fails_closed_no_position_written(pool: asyncpg.Pool) -> None:
    """negative — 거래소가 잔고 부족(INSUFFICIENT_FUNDS)으로 거부하면
    fail-closed: claim 행은 UNKNOWN으로 귀결되고(§3.4
    classify_submit_failure), positions에는 아무것도 안 남는다."""
    user_id = await create_test_tenant(pool)
    execution_id = await create_execution(pool, user_id)

    async def reject_insufficient_funds(_order: Order) -> Order:
        raise ExchangeError(ExchangeErrorKind.INSUFFICIENT_FUNDS, venue="paper_sim")

    adapter = FakeExchangeAdapter(
        exchange_name="paper_sim", is_paper_trading=True, on_place_order=reject_insufficient_funds
    )
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    order = _make_order(execution_id, "settle-insufficient")

    with pytest.raises(ExchangeError):
        await submit_order(order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=gate)

    async with pool.acquire() as conn:
        db_status = await conn.fetchval(
            "SELECT status FROM orders WHERE client_order_id = $1", order.client_order_id
        )
        position = await conn.fetchval(
            "SELECT 1 FROM positions WHERE execution_id = $1", execution_id
        )
    assert db_status == "UNKNOWN"
    assert position is None


async def test_unfilled_limit_order_does_not_write_position(pool: asyncpg.Pool) -> None:
    """negative — 미체결(ACKNOWLEDGED)로 끝난 주문은
    `record_fill_in_position_ledger`의 가드(FILLED가 아니면 아무 일도
    안 함)가 실제로 positions 쓰기를 막는다."""
    user_id = await create_test_tenant(pool)
    execution_id = await create_execution(pool, user_id)
    adapter = _make_adapter(place_order_result_status=OrderStatus.ACKNOWLEDGED)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    order = _make_order(
        execution_id, "settle-unfilled", order_type=OrderType.LIMIT, price=Decimal("100")
    )

    persisted = await submit_order(
        order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=gate
    )

    assert persisted.status is OrderStatus.ACKNOWLEDGED
    async with pool.acquire() as conn:
        position = await conn.fetchval(
            "SELECT 1 FROM positions WHERE execution_id = $1", execution_id
        )
    assert position is None


async def test_settlement_failure_propagates_not_swallowed(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """변조 케이스(실패 주입) — positions 정산이 내부적으로 깨지면(예:
    `record_fill_in_position_ledger` 예외) 그 실패가 `submit_order`
    호출자에게 조용히 삼켜지지 않고 그대로 전파돼야 한다. 이 몽키패치가
    없다면 위 happy-path 테스트의 "positions 행이 존재한다" 단언은
    정산이 성공했다는 증거가 되지만, 정산이 실패해도 예외 없이
    돌아온다면 그 단언은 아무것도 검증하지 못한 것이다 — 이 테스트는
    "정산이 깨지면 반드시 시끄럽게 실패한다"를 직접 고정한다."""
    import src.services.order_service.submit as submit_module

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated settlement failure")

    monkeypatch.setattr(submit_module, "record_fill_in_position_ledger", boom)

    user_id = await create_test_tenant(pool)
    execution_id = await create_execution(pool, user_id)
    adapter = _make_adapter()
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    order = _make_order(execution_id, "settle-mutation")

    with pytest.raises(RuntimeError, match="simulated settlement failure"):
        await submit_order(order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=gate)

    async with pool.acquire() as conn:
        db_status = await conn.fetchval(
            "SELECT status FROM orders WHERE client_order_id = $1", order.client_order_id
        )
        position = await conn.fetchval(
            "SELECT 1 FROM positions WHERE execution_id = $1", execution_id
        )
    # 주문 자체는 이미 체결·영속화됐다(거래소 왕복은 정산 실패 이전에 커밋) —
    # 정산만 깨졌다는 것을 별도로 증명한다(알려진 미검증 갭, position_ledger.py
    # 모듈 docstring "동시 첫 체결 경합" 항목과 같은 종류의 한계).
    assert db_status == "FILLED"
    assert position is None


async def _p95_ms(reps: int, step: Callable[[], Awaitable[object]]) -> float:
    samples: list[float] = []
    for _ in range(reps):
        t0 = time.perf_counter()
        await step()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    return samples[int(len(samples) * 0.95) - 1]


@pytest.mark.perf
async def test_submit_order_end_to_end_latency_within_normalized_budget(pool: asyncpg.Pool) -> None:
    """수치 성능 단언(D2) — ADR-2026-09-09-C 축별 성능 예산("주문
    제출->ACK p95 50ms(paper)")을 이 e2e 경로 전체(게이트 평가 -> claim
    -> 체결 -> positions 정산 -> 이벤트 발행)에 대해, 공유 CI 변동에
    강인하도록 이 풀의 `SELECT 1` p95에 정규화한 임계로 단언한다(절대 ms
    상수 대신 — test_paper_simulator_adapter.py와 동일 관례)."""
    reps = 10
    async with pool.acquire() as conn:
        baseline_p95 = await _p95_ms(reps, lambda: conn.fetchval("SELECT 1"))

    user_id = await create_test_tenant(pool)
    execution_id = await create_execution(pool, user_id)
    adapter = _make_adapter()
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    counter = {"n": 0}

    async def _one_submit() -> None:
        counter["n"] += 1
        order = _make_order(execution_id, f"perf-{counter['n']}")
        await submit_order(order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=gate)

    submit_p95 = await _p95_ms(reps, _one_submit)
    budget_ms = max(800.0, 80.0 * baseline_p95)
    print(  # noqa: T201 — 실측치는 비차단 기록, 게이트는 아래 assert.
        f"\nsubmit_order e2e p95={submit_p95:.3f}ms baseline(SELECT 1) "
        f"p95={baseline_p95:.3f}ms budget={budget_ms:.3f}ms"
    )
    assert submit_p95 < budget_ms
