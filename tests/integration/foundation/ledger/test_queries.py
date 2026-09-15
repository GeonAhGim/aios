"""LC-16 `application/queries.py` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.4, §4.4, §9 LC-16.
DoD: "프론트 무변경으로 기존 지갑 테스트 전부 통과" — `get_balance`가
`balance`(레거시)를 그대로 두고 `available`/`held`/`pending_payout`을
정확히 보고하는지, 그리고 레거시·원장 잔액이 어긋나면 500이 아니라
명시적 `WalletLedgerDriftError`로 표면화하는지(negative case)를 검증한다.

DEEPEN(task-2969, docs/audit/DEPTH_LA_LB_LC.md#613) — 원 task-613(D1)은
negative 1건(레거시>원장 드리프트)뿐이고 실패주입·성능단언·게이트적색
재현이 없었다. 아래를 추가해 D3로 올린다: negative 2건 추가(원장 계정이
아예 생성된 적 없는 경우, 레거시<원장 반대 방향 드리프트) + 실패주입
1건(스냅샷 SQL 왕복 중 커넥션 단절 시 조용한 기본값 대신 예외 전파) +
성능 단언 1건(p95 지연 예산) + 게이트 적색 재현 1건(task-951이 고친
"4개 독립 SELECT" 방식을 이 테스트 안에서 재현해 통제된 인터리빙으로
위양성 드리프트를 결정론적으로 발생시키고, 같은 최종 상태에서 수정된
단일 SQL 왕복 `get_balance`는 위양성이 없음을 대조). 코드 변경 없음
(`queries.py`는 task-951에서 이미 고쳐진 그대로).

DEEPEN(task-2989, docs/audit/DEPTH_LA_LB_LC.md#951) — 같은 파일을 다시
가리키는 별도 축 항목(원 task-951, D1)이 위 task-2969 증적으로도 채워지지
않는 요건 하나를 남겼다: "실패주입(드리프트가 실 SQL 데이터 손상으로
생성되어 모의/시뮬레이션 예외 아님)"였다 — task-2969가 추가한 실패주입은
`monkeypatch.setattr(asyncpg.connection.Connection, "fetch", ...)`로 커넥션
예외를 시뮬레이션한 것이라 이 항목을 채우지 못한다. 아래 1건을 추가한다:
`ledger_balance`(원장 진실)를 애플리케이션 계층(저널·감사 이중기록)을
완전히 우회해 테스트 코드가 직접 DELETE+INSERT(WORM 트리거가 막는 건
리터럴 UPDATE뿐 — FA-10, `a2c4f9e1b3d5_fa10_bitemporal_projections.py`)로
손상시켜, 진짜 SQL 데이터 손상만으로 드리프트가 발생하고 `get_balance`가
그 손상된 값을 진실로 오인하지 않고 fail-closed로 실패하는지 검증한다.
negative≥3·성능 단언·게이트 적색 재현은 task-2969가 이미 채워 그대로
유효하다. 코드 변경 없음.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.legacy_wallet_bridge import bridge_credit
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_hold_repository import PostgresHoldRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.purchase_flow import capture_hold, place_hold
from src.foundation.ledger.application.queries import WalletLedgerDriftError, get_balance
from src.foundation.ledger.contracts.v1 import UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from tests.integration.conftest import create_test_user

_TEST_PURPOSE = "TEST_QUERIES_PURCHASE"


def _clock() -> datetime:
    return datetime.now(timezone.utc)


class _RealPorts:
    def __init__(self, pool) -> None:
        self.journal = PostgresJournalRepository(pool)
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)
        self.holds = PostgresHoldRepository(pool)
        self.clock = _clock


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


async def test_get_balance_new_user_is_all_zero(pool, ports):
    user = await create_test_user(pool)

    result = await get_balance(pool, user, balances=ports.balances)

    assert result.user_id == user
    assert result.balance == Decimal("0")
    assert result.available == Decimal("0")
    assert result.held == Decimal("0")
    assert result.pending_payout == Decimal("0")


async def test_get_balance_reports_available_and_held_after_hold_placed(pool, ports):
    buyer = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, buyer, Decimal("100.00"), "TOPUP")

    reference = f"test-queries:{uuid4()}"
    async with pool.acquire() as conn, conn.transaction():
        await place_hold(
            conn,
            buyer_id=buyer,
            amount=Decimal("30.00"),
            purpose=_TEST_PURPOSE,
            reference=reference,
            expires_at=_clock() + timedelta(minutes=15),
            actor_subject_id=buyer,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
            holds=ports.holds,
        )
        # 실제 호출자(`src/services/purchase_service.py::_project`)라면 여기서
        # 레거시 투영도 함께 갱신한다 — `purchase_flow.place_hold` 자체는
        # `user_wallets`를 건드리지 않는다(모듈 docstring 참고). 이 테스트는
        # 그 계약을 그대로 재현해 드리프트 없는 상태를 유지한다.
        await conn.execute(
            "UPDATE user_wallets SET balance = balance - $2 WHERE user_id = $1",
            buyer,
            Decimal("30.00"),
        )

    result = await get_balance(pool, buyer, balances=ports.balances)
    assert result.balance == Decimal("70.00")
    assert result.available == Decimal("70.00")
    assert result.held == Decimal("30.00")
    assert result.pending_payout == Decimal("0")


async def test_get_balance_reports_pending_payout_after_capture(pool, ports):
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, buyer, Decimal("100.00"), "TOPUP")

    reference = f"test-queries:{uuid4()}"
    async with pool.acquire() as conn, conn.transaction():
        hold = await place_hold(
            conn,
            buyer_id=buyer,
            amount=Decimal("100.00"),
            purpose=_TEST_PURPOSE,
            reference=reference,
            expires_at=_clock() + timedelta(minutes=15),
            actor_subject_id=buyer,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
            holds=ports.holds,
        )
        await conn.execute(
            "UPDATE user_wallets SET balance = balance - $2 WHERE user_id = $1",
            buyer,
            Decimal("100.00"),
        )
        capture = await capture_hold(
            conn,
            hold,
            seller_id=seller,
            commission_rate=Decimal("0.15"),
            actor_subject_id=buyer,
            trace_id=uuid4(),
            now=_clock(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
            holds=ports.holds,
        )

    buyer_result = await get_balance(pool, buyer, balances=ports.balances)
    assert buyer_result.balance == Decimal("0.00")
    assert buyer_result.available == Decimal("0.00")
    assert buyer_result.held == Decimal("0")  # capture가 buyer HELD를 전부 비웠다

    seller_result = await get_balance(pool, seller, balances=ports.balances)
    assert seller_result.balance == Decimal("0")  # 레거시 투영은 아직 아무도 안 건드림
    assert seller_result.available == Decimal("0")  # seller AVAILABLE 계정 자체가 아직 없음
    assert seller_result.pending_payout == capture.payout_amount == Decimal("85.00")


async def test_get_balance_raises_explicit_drift_error_instead_of_generic_failure(pool, ports):
    """DoD negative test: 원장과 레거시 투영이 어긋나면 500이 아니라
    `WalletLedgerDriftError`로 명시적으로 실패해야 한다."""
    user = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, user, Decimal("50.00"), "TOPUP")

    # 원장 밖에서(운영자 수기 수정 등) 레거시 투영만 어긋나게 만든다 — 이
    # 브리지를 거치지 않은 직접 SQL이라 원장은 그대로 50.00으로 남는다.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_wallets SET balance = balance + $2 WHERE user_id = $1",
            user,
            Decimal("999.00"),
        )

    with pytest.raises(WalletLedgerDriftError) as exc_info:
        await get_balance(pool, user, balances=ports.balances)

    assert exc_info.value.user_id == user
    assert exc_info.value.legacy_balance == Decimal("1049.00")
    assert exc_info.value.ledger_available == Decimal("50.00")


async def test_get_balance_no_false_positive_drift_under_concurrent_commits(pool, ports):
    """task-951 결함 수정 검증: 조회 도중 다른 트랜잭션이 bridge_credit/
    place_hold를 커밋해도 위양성 `WalletLedgerDriftError`가 나면 안 된다.
    수정 전(4개의 개별 SELECT)에서는 legacy/ledger 읽기 사이에 커밋이
    끼어들면 서로 다른 시점의 값을 비교해 위양성 409를 던질 수 있었다 —
    이 테스트는 다량의 동시 커밋과 동시 조회를 경합시켜, `get_balance` 중
    하나라도 `WalletLedgerDriftError`를 던지면 `asyncio.gather`가 그대로
    전파해 테스트를 실패시킨다."""
    buyer = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, buyer, Decimal("1000.00"), "TOPUP")

    async def credit_writer() -> None:
        async with pool.acquire() as conn, conn.transaction():
            await bridge_credit(conn, buyer, Decimal("1.00"), "TOPUP")

    async def hold_writer() -> None:
        reference = f"test-queries-concurrent:{uuid4()}"
        async with pool.acquire() as conn, conn.transaction():
            await place_hold(
                conn,
                buyer_id=buyer,
                amount=Decimal("1.00"),
                purpose=_TEST_PURPOSE,
                reference=reference,
                expires_at=_clock() + timedelta(minutes=15),
                actor_subject_id=buyer,
                trace_id=uuid4(),
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
                holds=ports.holds,
            )
            await conn.execute(
                "UPDATE user_wallets SET balance = balance - $2 WHERE user_id = $1",
                buyer,
                Decimal("1.00"),
            )

    async def reader() -> None:
        await get_balance(pool, buyer, balances=ports.balances)

    writers = [credit_writer() for _ in range(15)] + [hold_writer() for _ in range(15)]
    readers = [reader() for _ in range(60)]
    await asyncio.gather(*writers, *readers)

    final = await get_balance(pool, buyer, balances=ports.balances)
    assert final.balance == final.available
    assert final.balance == Decimal("1000.00") + Decimal("15.00") - Decimal("15.00")
    assert final.held == Decimal("15.00")


# ---- DEEPEN(task-2969): negative 2건 추가 ----


async def test_get_balance_raises_drift_when_ledger_account_never_created(pool, ports):
    """DoD negative test 2/3: 레거시 투영(`user_wallets`)에는 값이 있지만
    대응하는 원장 AVAILABLE 계정이 아예 생성된 적 없는 경우(브리지를 한
    번도 거치지 않은 사용자 — 구버전 데이터 이관·수기 시드 등). 기존
    negative(bridge_credit으로 원장 계정을 만든 뒤 레거시만 어긋나게 함)와
    달리, `_BALANCE_SNAPSHOT_SQL`의 `LEFT JOIN`이 `ledger_account` 자체가
    없어 NULL을 반환하는 경로(모듈 docstring상 0으로 취급)에서도 드리프트가
    빠짐없이 잡히는지를 검증한다."""
    user = await create_test_user(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_wallets (user_id, balance) VALUES ($1, $2)",
            user,
            Decimal("77.00"),
        )

    with pytest.raises(WalletLedgerDriftError) as exc_info:
        await get_balance(pool, user, balances=ports.balances)

    assert exc_info.value.user_id == user
    assert exc_info.value.legacy_balance == Decimal("77.00")
    assert exc_info.value.ledger_available == Decimal("0")


async def test_get_balance_raises_drift_when_legacy_under_reports(pool, ports):
    """DoD negative test 3/3: 드리프트 비교(`legacy_balance != available`)는
    방향에 무관해야 한다 — 기존 negative는 레거시>원장 방향만 검증했다.
    이 테스트는 반대 방향(레거시가 원장보다 작게 어긋남)도 실제로 예외를
    던지는지 증명해, 구현이 한쪽 부등호로 실수로 좁혀지는 회귀를 잡는다."""
    user = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, user, Decimal("200.00"), "TOPUP")

    # 원장 밖에서 레거시 투영만 아래로 어긋나게 만든다 — 원장은 200.00 그대로.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_wallets SET balance = balance - $2 WHERE user_id = $1",
            user,
            Decimal("50.00"),
        )

    with pytest.raises(WalletLedgerDriftError) as exc_info:
        await get_balance(pool, user, balances=ports.balances)

    assert exc_info.value.legacy_balance == Decimal("150.00")
    assert exc_info.value.ledger_available == Decimal("200.00")
    assert exc_info.value.legacy_balance < exc_info.value.ledger_available


# ---- DEEPEN(task-2969): 실패주입 ----


async def test_get_balance_connection_failure_propagates_instead_of_silent_default(
    pool, ports, monkeypatch
):
    """실패주입: 스냅샷 SQL 왕복 도중 DB 커넥션이 끊기면(`PostgresConnectionError`)
    `get_balance`는 그 예외를 그대로 전파해야 한다 — 조용히 0이나 이전 값으로
    대체해 호출자가 정상 조회로 오인하게 만들면 안 된다(fail-closed, 모듈
    docstring의 드리프트 fail-closed 계약과 같은 원칙)."""
    user = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, user, Decimal("40.00"), "TOPUP")

    async def _boom(self, query, *args, **kwargs):
        raise asyncpg.PostgresConnectionError("injected connection failure")

    monkeypatch.setattr(asyncpg.connection.Connection, "fetch", _boom)
    try:
        with pytest.raises(asyncpg.PostgresConnectionError):
            await get_balance(pool, user, balances=ports.balances)
    finally:
        monkeypatch.undo()

    # 장애 주입 해제 후에는 정상 동작해야 한다 — 실패가 상태를 오염시키지 않았다.
    result = await get_balance(pool, user, balances=ports.balances)
    assert result.balance == Decimal("40.00")


# ---- DEEPEN(task-2989): 실패주입(실 SQL 데이터 손상, 모의 예외 아님) ----


async def test_get_balance_raises_drift_when_ledger_balance_row_directly_corrupted(pool, ports):
    """DoD failure-injection(task-951 요건): 드리프트를 python 모의 예외가
    아니라 실제 SQL 데이터 손상으로 만든다. `ledger_balance`는 WORM 트리거로
    리터럴 UPDATE가 막혀 있지만(FA-10, a2c4f9e1b3d5) DELETE+INSERT는
    허용된다 — `apply()` 자체가 갱신할 때 쓰는 패턴이다(postgres_balance_
    repository.py 참고). 이 테스트는 그 DELETE+INSERT를 애플리케이션 계층
    (저널 append·감사 이중기록·낙관적 락)을 완전히 우회해 테스트 코드가
    직접 실행함으로써 "원장(진실) 자체가 SQL 레벨에서 손상된" 실제 운영
    사고를 재현한다. 기존 negative 테스트들은 레거시(`user_wallets`) 쪽만
    어긋나게 했다 — 이 테스트는 반대로 원장 쪽이 실 SQL로 손상돼도
    `get_balance`가 손상된 값을 진실로 오인하지 않고 fail-closed로
    `WalletLedgerDriftError`를 던지는지 검증한다."""
    user = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, user, Decimal("300.00"), "TOPUP")

    available_code = ua(user, UserSub.AVAILABLE)
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT lb.account_id, lb.held, lb.pending_payout, lb.allow_negative, "
            "lb.last_entry_seq FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            available_code,
        )
        await conn.execute("DELETE FROM ledger_balance WHERE account_id = $1", row["account_id"])
        await conn.execute(
            "INSERT INTO ledger_balance (account_id, balance, held, pending_payout, "
            "allow_negative, last_entry_seq, updated_at) VALUES ($1, $2, $3, $4, $5, $6, now())",
            row["account_id"],
            Decimal("999999.00"),
            row["held"],
            row["pending_payout"],
            row["allow_negative"],
            row["last_entry_seq"] + 1,
        )

    with pytest.raises(WalletLedgerDriftError) as exc_info:
        await get_balance(pool, user, balances=ports.balances)

    assert exc_info.value.user_id == user
    assert exc_info.value.legacy_balance == Decimal("300.00")
    assert exc_info.value.ledger_available == Decimal("999999.00")


# ---- DEEPEN(task-2969): 성능 단언 ----


@pytest.mark.perf
async def test_get_balance_meets_latency_budget(pool, ports):
    """성능 단언: `get_balance` 단일 SQL 왕복(3계정 `LEFT JOIN`)의 p95
    지연이 예산 내여야 한다 — 무상한 지연 자체가 결함이다(LC-15b
    `test_post_chargeback_meets_latency_budget`와 같은 패턴)."""
    user = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, user, Decimal("500.00"), "TOPUP")

    budget_sec = 0.5  # 실측 로컬 단건 <0.01s, CI 편차 감안한 p95 상한
    samples = []
    for _ in range(50):
        start = time.perf_counter()
        await get_balance(pool, user, balances=ports.balances)
        samples.append(time.perf_counter() - start)

    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1]
    print(f"[LC-16 get_balance] p95={p95:.4f}s (budget<{budget_sec}s), n={len(samples)}")
    assert p95 < budget_sec, f"get_balance p95 지연이 예산({budget_sec}s)을 넘었습니다({p95:.4f}s)."


# ---- DEEPEN(task-2969): 게이트 적색 재현 ----


async def test_gate_red_pre_fix_four_query_read_reproduces_false_positive_drift(pool, ports):
    """게이트 적색 재현(task-951): 수정 전 코드가 하던 방식(레거시와 원장
    AVAILABLE을 독립된 SELECT 두 번으로 나눠 읽음)을 이 테스트 안에서 직접
    재현한다. 모든 쓰기(`bridge_credit`)가 legacy와 ledger를 한 트랜잭션
    안에서 원자적으로 함께 바꾸므로 진짜 드리프트는 전혀 없는데도, 두 읽기
    사이에 커밋이 끼어들도록 `asyncio.Event`로 인터리빙을 강제하면 순수
    타이밍만으로 위양성이 발생함을 결정론적으로 증명한다(무작위 경합에
    기대는 `test_get_balance_no_false_positive_drift_under_concurrent_commits`
    와 달리 100% 재현). 대조로, 같은 최종 상태에서 수정된 `get_balance`
    (단일 SQL 왕복)는 위양성이 없다 — task-951이 고친 결함이 실재했음을
    재현으로 보인다."""
    buyer = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, buyer, Decimal("100.00"), "TOPUP")

    available_code = ua(buyer, UserSub.AVAILABLE)
    legacy_read = asyncio.Event()
    commit_done = asyncio.Event()

    async def pre_fix_four_query_read(read_conn):
        legacy = await read_conn.fetchval(
            "SELECT balance FROM user_wallets WHERE user_id = $1", buyer
        )
        legacy_read.set()
        await commit_done.wait()
        available = await read_conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            available_code,
        )
        return legacy, available

    async def concurrent_writer() -> None:
        await legacy_read.wait()
        async with pool.acquire() as wconn, wconn.transaction():
            await bridge_credit(wconn, buyer, Decimal("1.00"), "TOPUP")
        commit_done.set()

    async with pool.acquire() as read_conn:
        (legacy, available), _ = await asyncio.gather(
            pre_fix_four_query_read(read_conn), concurrent_writer()
        )

    assert legacy == Decimal("100.00")
    assert available == Decimal("101.00")
    assert legacy != available, (
        "수정 전 4-쿼리 방식이 위양성 드리프트를 재현하지 못했습니다 — "
        "인터리빙이 강제되지 않았을 수 있습니다."
    )

    result = await get_balance(pool, buyer, balances=ports.balances)
    assert result.balance == result.available == Decimal("101.00")
