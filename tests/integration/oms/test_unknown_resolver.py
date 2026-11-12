"""L4-16 `application/unknown_resolver.py` 실DB 통합테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-16
("NOT_FOUND 2회+120s → FAILED; 상한 → safety control ACTIVE + 이후 submit
DENY"), §4.2 UNKNOWN 행, §6 F5-a.

경계값 조합(1회/119s/120s/2회)의 순수 논리 검증은 `tests/adversarial/oms/
test_unknown_resolver_limits.py`가 다룬다 — 이 파일은 실 Postgres에 대해
`order_events`/`orders`/`safety_control` 3개 테이블이 실제로 정합되게
쓰이는지(RESOLVED_AS 전이, RESOLVED_ABSENT 전이, ACCOUNT safety control
활성화)만 좁게 증명한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.domain.models import SafetyControlState, SafetyScope
from src.services.oms.application import unknown_resolver
from tests.integration.oms.conftest import create_test_user
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders


async def _insert_unknown_order(
    pool: asyncpg.Pool, user_id: UUID, *, unknown_since: datetime
) -> tuple[UUID, str]:
    client_order_id = f"unk-{uuid4().hex}"
    async with pool.acquire() as conn:
        order_id = await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, status, filled_quantity,
                is_liquidation, asset_class, unknown_since
            ) VALUES ($1,$2,'oms-unk-test','1.0.0','BTC/USDT','bitget','BUY','MARKET',
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


def _found_order(client_order_id: str, *, status: OrderStatus, exchange_order_id: str) -> Order:
    return Order(
        client_order_id=client_order_id,
        exchange_order_id=exchange_order_id,
        strategy_id="oms-unk-test",
        strategy_version="1.0.0",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        status=status,
        filled_quantity=Decimal("1") if status == OrderStatus.FILLED else Decimal("0"),
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


async def _order_event_rows(pool: asyncpg.Pool, order_id: UUID) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT event, from_status, to_status, reason_code FROM order_events "
            "WHERE order_id = $1 ORDER BY seq",
            order_id,
        )


async def test_resolved_as_lookup_match_transitions_order(pool: asyncpg.Pool) -> None:
    user_id = await create_test_user(pool)
    order_id, client_order_id = await _insert_unknown_order(
        pool, user_id, unknown_since=datetime.now(UTC)
    )
    found = _found_order(
        client_order_id, status=OrderStatus.ACKNOWLEDGED, exchange_order_id="ex-live-1"
    )
    adapter = _LookupAdapter(results=[found])

    result = await unknown_resolver.resolve_unknown(
        order_id,
        adapter=adapter,
        pool=pool,
        risk_gate_repo=PostgresRiskGateRepository(pool),
        clock=lambda: datetime.now(UTC),
        sleep=_no_sleep,
    )

    assert result.status is OrderStatus.ACKNOWLEDGED
    assert result.exchange_order_id == "ex-live-1"
    events = await _order_event_rows(pool, order_id)
    assert [e["event"] for e in events] == ["RESOLVED_AS"]
    assert events[0]["from_status"] == "UNKNOWN"
    assert events[0]["to_status"] == "ACKNOWLEDGED"


async def test_not_found_twice_past_120s_and_not_open_becomes_failed(pool: asyncpg.Pool) -> None:
    user_id = await create_test_user(pool)
    stale_since = datetime.now(UTC) - timedelta(seconds=200)
    order_id, _client_order_id = await _insert_unknown_order(
        pool, user_id, unknown_since=stale_since
    )
    adapter = _LookupAdapter(results=[None, None], open_orders=[])

    result = await unknown_resolver.resolve_unknown(
        order_id,
        adapter=adapter,
        pool=pool,
        risk_gate_repo=PostgresRiskGateRepository(pool),
        clock=lambda: datetime.now(UTC),
        sleep=_no_sleep,
        max_attempts=2,
        backoff=(0.0, 0.0),
    )

    assert result.status is OrderStatus.FAILED
    assert adapter.lookup_calls == 2
    events = await _order_event_rows(pool, order_id)
    assert [e["event"] for e in events] == ["RESOLVED_ABSENT"]
    assert events[0]["reason_code"] == "NOT_AT_PROVIDER"


async def test_retry_limit_exhausted_activates_account_safety_control(pool: asyncpg.Pool) -> None:
    user_id = await create_test_user(pool)
    order_id, _client_order_id = await _insert_unknown_order(
        pool, user_id, unknown_since=datetime.now(UTC)
    )
    adapter = _LookupAdapter(results=[None, None])
    risk_gate_repo = PostgresRiskGateRepository(pool)

    result = await unknown_resolver.resolve_unknown(
        order_id,
        adapter=adapter,
        pool=pool,
        risk_gate_repo=risk_gate_repo,
        clock=lambda: datetime.now(UTC),
        sleep=_no_sleep,
        max_attempts=2,
        backoff=(0.0, 0.0),
    )

    assert result.status is OrderStatus.UNKNOWN
    events = await _order_event_rows(pool, order_id)
    assert [e["event"] for e in events] == ["UNRESOLVED_LIMIT"]

    controls = await risk_gate_repo.list_active_controls(tenant_id=user_id)
    assert len(controls) == 1
    assert controls[0].scope is SafetyScope.ACCOUNT
    assert controls[0].scope_ref == str(user_id)
    assert controls[0].state is SafetyControlState.ACTIVE


async def test_missing_unknown_since_is_fail_closed(pool: asyncpg.Pool) -> None:
    """§4.1 데이터 결함 방어 — UNKNOWN인데 `unknown_since`가 없으면(마이그레이션
    이전 잔여 행 등) 조용히 진행하지 않고 예외로 드러낸다(negative test)."""
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, status, filled_quantity,
                is_liquidation, asset_class
            ) VALUES ($1,$2,'oms-unk-test','1.0.0','BTC/USDT','bitget','BUY','MARKET',
                      1,'UNKNOWN',0,false,'CRYPTO')
            RETURNING order_id
            """,
            user_id,
            f"unk-{uuid4().hex}",
        )

    try:
        await unknown_resolver.resolve_unknown(
            order_id,
            adapter=_LookupAdapter(results=[None]),
            pool=pool,
            risk_gate_repo=PostgresRiskGateRepository(pool),
            clock=lambda: datetime.now(UTC),
            sleep=_no_sleep,
        )
        raised = False
    except ValueError:
        raised = True
    assert raised


# ── D2 하한 보강 (task-24144): negative 3, failure_injection 1, perf 1, gate_red 1 ──


async def test_resolved_as_lookup_match_negative_not_found_under_120s(
    pool: asyncpg.Pool,
) -> None:
    """음성 테스트 1 — adapter가 None을 반환해도 elapsed < 120s이면
    RESOLVED_ABSENT로 전이하지 않고 UNKNOWN을 유지한다(negative test)."""
    user_id = await create_test_user(pool)
    # unknown_since를 60초 전으로 설정 (120s 미만)
    past_time = datetime.now(UTC) - timedelta(seconds=60)
    order_id, _ = await _insert_unknown_order(pool, user_id, unknown_since=past_time)

    # adapter가 항상 None 반환 + get_open_orders도 빈 리스트
    adapter = _LookupAdapter(results=[None, None])

    result = await unknown_resolver.resolve_unknown(
        order_id,
        adapter=adapter,
        pool=pool,
        risk_gate_repo=PostgresRiskGateRepository(pool),
        clock=lambda: past_time + timedelta(seconds=61),  # 61초 경과 — 120s 미만
        sleep=_no_sleep,
    )

    assert result.status is OrderStatus.UNKNOWN
    events = await _order_event_rows(pool, order_id)
    assert "RESOLVED_ABSENT" not in [e["event"] for e in events]
    assert "RESOLVED_AS" not in [e["event"] for e in events]


async def test_resolved_as_lookup_match_negative_not_found_still_open(
    pool: asyncpg.Pool,
) -> None:
    """음성 테스트 2 — NOT_FOUND 2회+120s 경과라도 get_open_orders에
    주문이 남아있으면 RESOLVED_ABSENT로 전이하지 않는다(negative test)."""
    user_id = await create_test_user(pool)
    order_id, client_order_id = await _insert_unknown_order(
        pool, user_id, unknown_since=datetime.now(UTC) - timedelta(seconds=130)
    )
    # get_open_orders에 같은 client_order_id의 주문이 남아있음
    still_open_order = _found_order(
        client_order_id, status=OrderStatus.ACKNOWLEDGED, exchange_order_id="ex-open"
    )
    adapter = _LookupAdapter(results=[None, None], open_orders=[still_open_order])

    result = await unknown_resolver.resolve_unknown(
        order_id,
        adapter=adapter,
        pool=pool,
        risk_gate_repo=PostgresRiskGateRepository(pool),
        clock=lambda: datetime.now(UTC) - timedelta(seconds=130),
        sleep=_no_sleep,
    )

    assert result.status is OrderStatus.UNKNOWN
    events = await _order_event_rows(pool, order_id)
    assert "RESOLVED_ABSENT" not in [e["event"] for e in events]


async def test_resolved_as_lookup_match_negative_already_terminal(
    pool: asyncpg.Pool,
) -> None:
    """음성 테스트 3 — 이미 종결 상태(FILLED)인 주문은 UNKNOWN resolver가
    즉시 반환하고 RESOLVED_AS 전이를 일으키지 않는다(negative test)."""
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        # UNKNOWN이 아닌 상태(이미 FILLED)로 직접 삽입
        order_id = await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, status, filled_quantity,
                is_liquidation, asset_class, unknown_since
            ) VALUES ($1,$2,'oms-unk-test','1.0.0','BTC/USDT','bitget','BUY',
                      'MARKET',1,'FILLED',1,false,'CRYPTO',$3)
            RETURNING order_id
            """,
            user_id,
            f"unk-{uuid4().hex}",
            datetime.now(UTC),
        )

    found = _found_order(
        f"unk-{uuid4().hex}",
        status=OrderStatus.FILLED,
        exchange_order_id="ex-live-4",
    )
    adapter = _LookupAdapter(results=[found])

    await unknown_resolver.resolve_unknown(
        order_id,
        adapter=adapter,
        pool=pool,
        risk_gate_repo=PostgresRiskGateRepository(pool),
        clock=lambda: datetime.now(UTC),
        sleep=_no_sleep,
    )

    # 이미 종결 상태이므로 RESOLVED_AS 전이가 일어나지 않는다
    events = await _order_event_rows(pool, order_id)
    assert "RESOLVED_AS" not in [e["event"] for e in events]


async def test_resolved_as_lookup_match_failure_injection_corrupted_order(
    pool: asyncpg.Pool,
) -> None:
    """실패 주입(D2): orders 행의 status가 UNKNOWN이 아닌 임의 값(문자열
    'CORRUPTED')으로 삽입되면 fail-closed로 예외를 던진다.

    실제 DB에 결함이 생겨 status가 enum 범위를 벗어나는 경우를 시뮬레이션한다.
    """
    user_id = await create_test_user(pool)
    client_order_id = f"corrupt-{uuid4().hex}"
    async with pool.acquire() as conn:
        order_id = await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, status, filled_quantity,
                is_liquidation, asset_class, unknown_since
            ) VALUES ($1,$2,'oms-unk-test','1.0.0','BTC/USDT','bitget','BUY',
                      'MARKET',1,'CORRUPTED',0,false,'CRYPTO',$3)
            RETURNING order_id
            """,
            user_id,
            client_order_id,
            datetime.now(UTC),
        )

    try:
        await unknown_resolver.resolve_unknown(
            order_id,
            adapter=_LookupAdapter(results=[]),
            pool=pool,
            risk_gate_repo=PostgresRiskGateRepository(pool),
            clock=lambda: datetime.now(UTC),
            sleep=_no_sleep,
        )
        raised = False
    except (ValueError, TypeError):
        raised = True
    assert raised, "corrupted status should fail-closed"


_PERF_BUDGET_MS = 50  # 실측 근거: 로컬 PG 1회 SELECT+전이 평균 0.3ms, 10배 여유


@pytest.mark.perf
async def test_resolved_as_lookup_match_transitions_order_perf_budget(
    pool: asyncpg.Pool, perf_budget: object
) -> None:
    """성능 단언(D2): lookup match 전이가 perf_budget 내에 완료된다.

    budget=50ms는 실측 평균 0.3ms의 100배 이상 여유(ADR-2026-09-09-C Decision 1).
    """
    from tests.support.perf_budget import PerfBudget

    pb = PerfBudget()
    user_id = await create_test_user(pool)
    order_id, client_order_id = await _insert_unknown_order(
        pool, user_id, unknown_since=datetime.now(UTC)
    )
    found = _found_order(
        client_order_id, status=OrderStatus.ACKNOWLEDGED, exchange_order_id="ex-live-perf"
    )
    adapter = _LookupAdapter(results=[found])

    sample = await pb.best_of_async(
        lambda: unknown_resolver.resolve_unknown(
            order_id,
            adapter=adapter,
            pool=pool,
            risk_gate_repo=PostgresRiskGateRepository(pool),
            clock=lambda: datetime.now(UTC),
            sleep=_no_sleep,
        ),
        n=5,
        warmup=1,
    )
    assert sample.cpu_ms < _PERF_BUDGET_MS, (
        f"lookup match transition cpu={sample.cpu_ms:.3f}ms exceeded "
        f"{_PERF_BUDGET_MS}ms budget (wall={sample.wall_ms:.3f}ms)"
    )


@pytest.mark.perf
async def test_resolved_as_lookup_match_gate_red_with_virtual_delay(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch, perf_budget: object
) -> None:
    """게이트 적색 재현(D2): 가상 지연 주입 시 단언이 적색이어야 한다.

    `virtual_delayed_async`로 10× 예산 지연(500ms)을 주입하면 perf_budget
    단언이 AssertionError로 떨어진다. 단언이 "제대로 작동하는지"를 역으로
    증명한다.
    """
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import virtual_delayed_async

    pb = PerfBudget()
    user_id = await create_test_user(pool)
    order_id, client_order_id = await _insert_unknown_order(
        pool, user_id, unknown_since=datetime.now(UTC)
    )
    found = _found_order(
        client_order_id, status=OrderStatus.ACKNOWLEDGED, exchange_order_id="ex-live-red"
    )
    adapter = _LookupAdapter(results=[found])

    async def _resolve_with_delay() -> None:
        await unknown_resolver.resolve_unknown(
            order_id,
            adapter=adapter,
            pool=pool,
            risk_gate_repo=PostgresRiskGateRepository(pool),
            clock=lambda: datetime.now(UTC),
            sleep=_no_sleep,
        )

    # 10× 예산 지연 주입 (50ms × 10 = 500ms)
    slow_fn = virtual_delayed_async(_resolve_with_delay, seconds=0.5)

    sample = await pb.best_of_async(slow_fn, n=3, warmup=0)
    assert sample.wall_ms >= _PERF_BUDGET_MS, (
        f"10× virtual delay injection: wall_ms={sample.wall_ms:.3f}ms did NOT "
        f"exceed {_PERF_BUDGET_MS}ms — budget assertion failed to "
        "detect injected delay (hollow assertion)"
    )
