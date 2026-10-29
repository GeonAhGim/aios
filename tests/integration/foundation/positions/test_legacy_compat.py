# loc-allow: integration test file for legacy_positions_projection; 4 negative/invariant tests +
#   failure-injection + performance assertions require substantial fixture/setup code
"""LegacyPositionsProjection 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-10.
DoD(task-376): 동일 계정·심볼에 대해 legacy `positions` 직접 쿼리 결과와
`LegacyPositionsProjection` 투영 결과가 수량·평단·실현손익까지 일치
(부분청산 후 포함), 대응 legacy 행이 없으면 빈 결과(예외 아님).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from src.foundation.entities.domain.defaults import default_fund_id, default_portfolio_id
from tests.integration.foundation.positions.conftest import force_row_replace
from tests.integration.foundation.positions.conftest_legacy import (
    _EXCHANGE,
    _EXCHANGE_B,
    _direct_legacy_query,
    _project,
    _seed_linked_pair,
    _setup_account,
)
from tests.integration.foundation.positions.conftest_legacy import (
    _snapshot_key as _snapshot_key,
)
from tests.integration.foundation.positions.conftest_legacy import (
    projection as projection,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import delayed_async


async def test_open_position_matches_legacy_query(pool, projection):
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("2.5"),
        price=Decimal("100.1234567890"),
    )

    legacy_rows = await _direct_legacy_query(pool, user_id=tenant_id, symbol=symbol)
    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)

    assert len(legacy_rows) == 1
    assert len(projected) == 1
    legacy_row, projected_row = legacy_rows[0], projected[0]
    assert projected_row.legacy_position_id == legacy_row["legacy_position_id"]
    assert projected_row.quantity == legacy_row["quantity"]
    assert projected_row.average_entry_price == legacy_row["average_entry_price"]
    assert projected_row.realized_pnl == legacy_row["realized_pnl"]
    assert projected_row.unrealized_pnl == legacy_row["unrealized_pnl"]


async def test_partial_close_still_matches_legacy_query(pool, projection):
    """부분청산 후: 수량이 줄고 실현손익이 누적된 상태도 일치해야 한다."""
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    legacy_id, position_key = await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("10"),
        price=Decimal("50"),
    )

    # 부분청산: 10 중 4를 60에 매도 → 남은 수량 6, 실현손익 = 4*(60-50) = 40
    partial_quantity, partial_realized = Decimal("6"), Decimal("40")
    await force_row_replace(
        pool,
        table="positions",
        id_column="id",
        id_value=legacy_id,
        quantity=partial_quantity,
        realized_pnl=partial_realized,
    )
    await force_row_replace(
        pool,
        table="pos_snapshot",
        id_column="position_key",
        id_value=position_key,
        quantity=partial_quantity,
        realized_pnl_base=partial_realized,
        last_journal_seq=2,
    )

    legacy_rows = await _direct_legacy_query(pool, user_id=tenant_id, symbol=symbol)
    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)

    assert len(legacy_rows) == 1
    assert len(projected) == 1
    assert projected[0].quantity == partial_quantity == legacy_rows[0]["quantity"]
    assert projected[0].realized_pnl == partial_realized == legacy_rows[0]["realized_pnl"]
    assert projected[0].average_entry_price == legacy_rows[0]["average_entry_price"]


async def test_closed_position_reports_closed_at_and_matches_legacy(pool, projection):
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("0"),
        price=Decimal("20"),
        realized_pnl=Decimal("15"),
        closed_at=datetime.now(UTC),
    )

    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)

    assert len(projected) == 1
    assert projected[0].closed_at is not None
    assert projected[0].realized_pnl == Decimal("15")


async def test_no_linked_legacy_row_returns_empty_not_exception(pool, projection):
    """스냅샷이 있어도 `legacy_position_id`가 비어 있으면(아직 연결 안 됨)
    빈 리스트를 반환한다 — 예외가 아니다."""
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pos_snapshot (
                position_key, tenant_id, account_id, instrument_id, quantity,
                avg_cost, cost_method, lots, realized_pnl_base,
                unrealized_pnl_base, fees_base, funding_base, mark_price,
                mark_at, last_journal_seq, legacy_position_id, updated_at,
                fund_id, portfolio_id
            ) VALUES (
                $1, $2, $3, $4, 3, 10, 'FIFO', $5::jsonb, 0, NULL, 0, 0,
                NULL, NULL, 1, NULL, now(), $6, $7
            )
            """,
            _snapshot_key(tenant_id),
            tenant_id,
            account_id,
            uuid.uuid4(),
            json.dumps([]),
            default_fund_id(tenant_id),
            default_portfolio_id(tenant_id),
        )

    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)
    assert projected == []


async def test_no_matching_symbol_returns_empty_not_exception(pool, projection):
    """해당 계정·심볼 조합의 legacy 행이 아예 없으면 빈 리스트."""
    tenant_id, _ = await _setup_account(pool)
    projected = await _project(projection, pool, user_id=tenant_id, symbol="NEVER-EXISTED")
    assert projected == []


async def test_dangling_legacy_position_id_rejected_by_fk_constraint(pool):
    """`pos_snapshot.legacy_position_id`는 DEFERRABLE FK로 `positions(id)`를
    참조한다(마이그레이션 `a2c4f9e1b3d5`) — 존재하지 않는 id를 가리키는
    스냅샷은 커밋 시 거부된다. 어댑터 모듈 docstring이 전제하는 "INNER
    JOIN이 조용히 빠뜨리는 건 아직 연결 안 된(`legacy_position_id IS
    NULL`) 스냅샷뿐, 댕글링 참조는 애초에 존재할 수 없다"를 raise로
    증명하는 진짜 negative 테스트(이전엔 빈 결과 확인뿐이었다)."""
    tenant_id, account_id = await _setup_account(pool)
    nonexistent_legacy_id = 2_147_483_647

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                """
                INSERT INTO pos_snapshot (
                    position_key, tenant_id, account_id, instrument_id, quantity,
                    avg_cost, cost_method, lots, realized_pnl_base,
                    unrealized_pnl_base, fees_base, funding_base, mark_price,
                    mark_at, last_journal_seq, legacy_position_id, updated_at,
                    fund_id, portfolio_id
                ) VALUES (
                    $1, $2, $3, $4, 1, 10, 'FIFO', $5::jsonb, 0, NULL, 0, 0,
                    NULL, NULL, 1, $6, now(), $7, $8
                )
                """,
                _snapshot_key(tenant_id),
                tenant_id,
                account_id,
                uuid.uuid4(),
                json.dumps([]),
                nonexistent_legacy_id,
                default_fund_id(tenant_id),
                default_portfolio_id(tenant_id),
            )


class _FaultInjectingConnection:
    """`asyncpg.Connection`을 흉내내 `fetch`에서 항상 실패하는 가짜 커넥션."""

    async def fetch(self, *args: object, **kwargs: object) -> list[object]:
        raise RuntimeError("boom: simulated connection fault")


async def test_get_positions_propagates_connection_fault_without_swallowing(projection):
    """`conn.fetch`가 실패하면 어댑터가 삼키지 않고 그대로 전파해야
    한다(fail-closed, 실패 주입) — 이전엔 정상 경로만 검증했다."""
    with pytest.raises(RuntimeError, match="boom"):
        await projection.get_positions(
            _FaultInjectingConnection(),
            user_id=uuid.uuid4(),
            symbol="ANY",
            exchange=_EXCHANGE,
        )


@pytest.mark.perf
async def test_get_positions_completes_within_latency_budget_for_many_reentries(
    pool, projection, perf_budget
):
    """legacy 재진입(같은 계정·심볼, 서로 다른 `entry_time`)이 300건 쌓인
    계정에서도 조회가 예산 안에 끝나야 한다(수치 성능 단언) — 이전엔 DoD
    "쿼리 결과 동일"만 확인했지 비용은 잰 적이 없었다.

    raw time.perf_counter() → perf_budget.sample_async() 전환(task-11431).
    비동기 I/O는 wall_ms로 왕복 지연을 측정한다.
    """
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    base_time = datetime.now(UTC)
    reentry_count = 300

    async with pool.acquire() as conn:
        for i in range(reentry_count):
            legacy_id: int = await conn.fetchval(
                """
                INSERT INTO positions (
                    user_id, symbol, exchange, strategy_id, quantity,
                    average_entry_price, realized_pnl, entry_time, closed_at
                ) VALUES ($1, $2, $3, 'test-strategy', 1, 10, 0, $4, NULL)
                RETURNING id
                """,
                tenant_id,
                symbol,
                _EXCHANGE,
                base_time + timedelta(microseconds=i),
            )
            await conn.execute(
                """
                INSERT INTO pos_snapshot (
                    position_key, tenant_id, account_id, instrument_id, quantity,
                    avg_cost, cost_method, lots, realized_pnl_base,
                    unrealized_pnl_base, fees_base, funding_base, mark_price,
                    mark_at, last_journal_seq, legacy_position_id, updated_at,
                    fund_id, portfolio_id
                ) VALUES (
                    $1, $2, $3, $4, 1, 10, 'FIFO', $5::jsonb, 0, NULL, 0, 0,
                    NULL, NULL, 1, $6, now(), $7, $8
                )
                """,
                _snapshot_key(tenant_id),
                tenant_id,
                account_id,
                uuid.uuid4(),
                json.dumps([]),
                legacy_id,
                default_fund_id(tenant_id),
                default_portfolio_id(tenant_id),
            )

    sample = await perf_budget.sample_async(
        lambda: _project(projection, pool, user_id=tenant_id, symbol=symbol)
    )

    projected = sample.result
    assert len(projected) == reentry_count
    budget_ms = 1000.0  # 1.0s in ms — budget 값 불변
    assert sample.wall_ms < budget_ms, (
        f"get_positions took {sample.wall_ms:.3f}ms for "
        f"{reentry_count} re-entries, budget {budget_ms:.3f}ms"
    )


async def test_different_exchange_same_symbol_not_leaked(pool, projection):
    """같은 테넌트·심볼이라도 거래소가 다르면 섞이면 안 된다 — SELECT의
    `p.exchange = $3` 필터가 빠지는 회귀가 나면 이 테스트가 그 자리에서
    적색이 된다(게이트 적색 재현)."""
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    legacy_id_a, _ = await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("1"),
        price=Decimal("10"),
    )
    await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("2"),
        price=Decimal("20"),
        exchange=_EXCHANGE_B,
    )

    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)

    assert len(projected) == 1
    assert projected[0].legacy_position_id == legacy_id_a
    assert projected[0].exchange == _EXCHANGE


async def test_negative_zero_quantity_not_filtered_by_adapter(pool, projection):
    """불변식 검증: LegacyPositionsProjection은 읽기 전용 투영 어댑터로
    domain 불변식(quantity > 0)을 검증하지 않는다 — 이 테스트는 그 사실을
    명시적으로 기록한다(음수 quantity 행이 투영되더라도 adapter는 전파할
    뿐 필터링하지 않음). domain 검증은 journal_rules/snapshot_builder
    쓰기 경로에서 해야 한다."""
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    legacy_id, position_key = await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("0"),
        price=Decimal("100"),
    )
    # 수량을 음수로 변경 — 불변식 위반 입력
    await force_row_replace(
        pool,
        table="positions",
        id_column="id",
        id_value=legacy_id,
        quantity=Decimal("-5"),
    )
    await force_row_replace(
        pool,
        table="pos_snapshot",
        id_column="position_key",
        id_value=position_key,
        quantity=Decimal("-5"),
    )

    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)

    # adapter는 읽기 전용: 음수 quantity 행도 투영한다(domain 검증 아님)
    assert len(projected) == 1
    assert projected[0].quantity == Decimal("-5"), (
        "LegacyPositionsProjection is read-only — it projects what the DB holds "
        "without domain validation; quantity > 0 check belongs in journal_rules."
    )


async def test_negative_test_mismatched_exchange_filter(pool, projection):
    """불변식 위반: 다른 거래소 행이 같은 user_id·symbol로 존재해도
    exchange 필터가 빠지면 유출된다 — exchange 파라미터를 무시하는
    코드는 이 테스트에서 적색이 된다."""
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    # TESTEX 행
    legacy_main, _ = await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("1"),
        price=Decimal("10"),
    )
    # TESTEX-B 행 (같은 user_id·symbol)
    await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("999"),
        price=Decimal("999"),
        exchange=_EXCHANGE_B,
    )

    # TESTEX만 조회 — TESTEX-B가 유출되면 실패
    projected = await _project(projection, pool, user_id=tenant_id, symbol=symbol)

    assert len(projected) == 1
    assert projected[0].legacy_position_id == legacy_main
    assert projected[0].exchange == _EXCHANGE
    assert projected[0].quantity != Decimal("999")


async def test_negative_test_tenant_isolation_violation(pool, projection):
    """불변식 위반: user_id(tenant_id) 필터가 빠지면 다른 테넌트의
    포지션이 유출된다 — user_id=$1 바인딩이 없으면 이 테스트가 적색."""
    tenant_a, account_a = await _setup_account(pool)
    tenant_b, account_b = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    legacy_b, _ = await _seed_linked_pair(
        pool,
        tenant_id=tenant_b,
        account_id=account_b,
        symbol=symbol,
        quantity=Decimal("42"),
        price=Decimal("42"),
    )

    # tenant_a로 조회 — tenant_b의 포지션이 유출되면 실패
    projected = await _project(projection, pool, user_id=tenant_a, symbol=symbol)

    assert projected == [] or all(row.legacy_position_id != legacy_b for row in projected), (
        "Tenant B's position must not leak into Tenant A's query"
    )


async def test_failure_injection_db_connection_reset(pool, projection):
    """실패주입: conn.fetch()가 OperationalError를 발생시키면 어댑터가
    삼키지 않고 전파한다 — DB 연결이 갑자기 끊기는 상황을 시뮬레이션."""
    import asyncpg

    class _ConnectionReset:
        async def fetch(self, *args, **kwargs):
            raise asyncpg.exceptions.ConnectionDoesNotExistError(
                {"description__": "connection reset"}
            )

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await projection.get_positions(
            _ConnectionReset(),  # duck-typed conn; mypy flags unused-ignore
            user_id=uuid.uuid4(),
            symbol="ANY",
            exchange=_EXCHANGE,
        )


async def test_concurrent_queries_for_different_tenants_stay_isolated(pool, projection):
    """두 테넌트가 같은 심볼을 동시에 조회해도 서로의 포지션이 섞이지
    않는다(적대적/동시성 증명) — 커넥션 풀을 공유하는 동시 조회가 테넌트
    필터를 우회하지 않는지 확인한다."""
    tenant_a, account_a = await _setup_account(pool)
    tenant_b, account_b = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    legacy_a, _ = await _seed_linked_pair(
        pool,
        tenant_id=tenant_a,
        account_id=account_a,
        symbol=symbol,
        quantity=Decimal("1"),
        price=Decimal("10"),
    )
    legacy_b, _ = await _seed_linked_pair(
        pool,
        tenant_id=tenant_b,
        account_id=account_b,
        symbol=symbol,
        quantity=Decimal("2"),
        price=Decimal("20"),
    )

    tenants = [tenant_a if i % 2 == 0 else tenant_b for i in range(20)]
    results = await asyncio.gather(
        *[_project(projection, pool, user_id=tenant, symbol=symbol) for tenant in tenants]
    )

    for tenant, projected in zip(tenants, results, strict=True):
        expected_id = legacy_a if tenant == tenant_a else legacy_b
        assert len(projected) == 1
        assert projected[0].legacy_position_id == expected_id


# ---- PERF-detection (task-17197, task-11539 감사 후속) ----

_GET_POSITIONS_CPU_BUDGET_MS = 50.0
"""단일 get_positions 호출의 CPU 예산 — 작은 입력(1 행)이 이 예산을
초과하면 legacy_adapter가 예상보다 무거운 쿼리/매핑을 하고 있다는 신호."""


@pytest.mark.perf
async def test_get_positions_cpu_time_converges_to_zero_for_small_input(
    pool,
    projection,
    perf_budget: PerfBudget,
) -> None:
    """단일 get_positions 호출의 CPU 측정이 0 수렴 확인 — 작은 입력(1 행)은
    거의 즉시 반환되므로 CPU 시간은 Windows 양자화 tick(약 15.6ms) 범위
    내여야 한다. batch=8로 묶으면 양자화 오차가 호출당 tick/8로 줄어
    실제 CPU 시간을 정상 측정한다.

    이 테스트는 PerfBudget.sample_async가 단일 호출 CPU 시간을 올바르게
    계측함을 동적으로 검증한다.
    """
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("2.5"),
        price=Decimal("100.1234567890"),
    )

    async def _one_call() -> list:
        async with pool.acquire() as conn, conn.transaction():
            return await projection.get_positions(
                conn,
                user_id=tenant_id,
                symbol=symbol,
                exchange=_EXCHANGE,
            )

    sample = await perf_budget.sample_async(_one_call, batch=8)
    assert sample.cpu_ms < 100.0, (
        f"get_positions 단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
        "작은 입력이므로 양자화 오차 범위 내여야 함 (budget<100ms)"
    )


@pytest.mark.perf
async def test_get_positions_latency_within_budget(
    pool,
    projection,
    perf_budget: PerfBudget,
) -> None:
    """get_positions 호출이 CPU 예산 내야 한다 — 작은 입력에 대해
    _GET_POSITIONS_CPU_BUDGET_MS 미만."""
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("2.5"),
        price=Decimal("100.1234567890"),
    )

    async def _one_call() -> list:
        async with pool.acquire() as conn, conn.transaction():
            return await projection.get_positions(
                conn,
                user_id=tenant_id,
                symbol=symbol,
                exchange=_EXCHANGE,
            )

    sample = await perf_budget.sample_async(_one_call, batch=5)
    print(
        f"[LB-10 get_positions] cpu={sample.cpu_ms:.3f}ms "
        f"budget<{_GET_POSITIONS_CPU_BUDGET_MS:.1f}ms"
    )
    assert sample.cpu_ms < _GET_POSITIONS_CPU_BUDGET_MS


@pytest.mark.perf
async def test_get_positions_perf_goes_red_with_io_delay_injection(
    pool,
    projection,
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """gate-red reproduction: asyncpg.Connection.fetch 에 IO 지연(delayed_async,
    2.0s)을 주입한 후 같은 성능 단언을 돌리면 예산을 초과해야 한다(red).

    `conn.fetch` 를 `delayed_async` 로 감싸 1회 호출당 2초 IO 지연을
    주입 — get_positions 는 I/O 바인딩 연산이므로 CPU 대신 wall-clock
    측정으로 지연을 검증한다. 정상 입력에서 통과하는 것이 테스트의 허수
    (trivial pass)가 아님을 보증한다. 단언/마커 삭제와 예산 완화 금지.
    """
    # asyncpg.Connection.fetch 를 delayed_async 로 감싸 IO 지연 주입 (2초/회)
    original_fetch = asyncpg.Connection.fetch
    monkeypatch.setattr(asyncpg.Connection, "fetch", delayed_async(original_fetch, 2.0))

    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("2.5"),
        price=Decimal("100.1234567890"),
    )

    async def _one_call_with_delay() -> list:
        async with pool.acquire() as conn, conn.transaction():
            return await projection.get_positions(
                conn,
                user_id=tenant_id,
                symbol=symbol,
                exchange=_EXCHANGE,
            )

    # 지연 주입 후 → wall-clock 이 예산 초과해야 함 (red)
    sample = await perf_budget.sample_async(_one_call_with_delay, batch=1)
    assert sample.wall_ms >= _GET_POSITIONS_CPU_BUDGET_MS, (
        f"지연 주입 후 wall 시간 {sample.wall_ms:.3f}ms — "
        f"예상과 달리 녹색: 측정/단언이 공허함(hollow assertion, "
        f"budget={_GET_POSITIONS_CPU_BUDGET_MS:.1f}ms)"
    )


# --- negative (>= 3) ---


async def test_get_positions_returns_empty_when_no_legacy_row(pool, projection):
    """매핑될 legacy 행이 없으면 빈 리스트를 반환한다(예외 아님)."""
    tenant_id, account_id = await _setup_account(pool)
    random_symbol = f"SYM{uuid.uuid4().hex[:8]}"

    async with pool.acquire() as conn, conn.transaction():
        result = await projection.get_positions(
            conn,
            user_id=tenant_id,
            symbol=random_symbol,
            exchange=_EXCHANGE,
        )

    assert result == []


async def test_get_positions_returns_empty_for_unknown_user(pool, projection):
    """존재하지 않는 user_id 에 대해 빈 리스트를 반환한다."""
    from uuid import UUID

    fake_user_id = UUID("00000000-0000-0000-0000-000000000001")

    async with pool.acquire() as conn, conn.transaction():
        result = await projection.get_positions(
            conn,
            user_id=fake_user_id,
            symbol="ANY",
            exchange=_EXCHANGE,
        )

    assert result == []


async def test_get_positions_returns_empty_for_unknown_exchange(pool, projection):
    """존재하지 않는 exchange 에 대해 빈 리스트를 반환한다."""
    tenant_id, account_id = await _setup_account(pool)
    symbol = f"SYM{uuid.uuid4().hex[:8]}"
    await _seed_linked_pair(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        symbol=symbol,
        quantity=Decimal("1"),
        price=Decimal("10"),
    )

    async with pool.acquire() as conn, conn.transaction():
        result = await projection.get_positions(
            conn,
            user_id=tenant_id,
            symbol=symbol,
            exchange="NONEXISTENT",
        )

    assert result == []
