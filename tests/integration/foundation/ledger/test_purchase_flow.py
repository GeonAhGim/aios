"""LC-13 `purchase_flow`/`purchase_service` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.5, §9 LC-13.
DoD(task-424): 동시 5건 구매 중 1건만 성공(나머지는 HOLD 충돌 거부) + 무료
리스팅은 분개 0건 + 기존 `tests/integration/test_marketplace_router.py`
무수정 통과(이 파일은 그 회귀를 건드리지 않는다 — 별도로 확인됨).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from scripts import replay_verify
from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_hold_repository import PostgresHoldRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.purchase_flow import (
    CaptureResult,
    HoldConflictError,
    capture_hold,
    place_hold,
    release_hold,
)
from src.foundation.ledger.contracts.v1 import HoldState, HoldView, UserSub
from src.foundation.ledger.domain.balance_rules import InsufficientAvailableError
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.hold_state import HoldExpiredError, IllegalHoldTransitionError
from src.services.listing_service import ListingService
from src.services.purchase_service import PurchaseService
from src.services.verification_service import VerificationService
from tests.integration.conftest import create_test_user
from tests.support.ledger_seed import seed_user_available_balance

_TEST_PURPOSE = "TEST_MARKETPLACE_PURCHASE"


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


async def _seed_available(pool, user_id: UUID, amount: Decimal) -> None:
    """`user_wallets`와 `ledger_balance`를 처음부터 일치시켜 픽스처를 만든다
    — 매 호출이 fresh `user_id`를 쓰므로(동시성 테스트가 검증하려는 것은
    `ledger_hold` UNIQUE 충돌이지 드리프트 재동기화 자체가 아니다) 항상
    드리프트 0에서 시작한다. FA-15a(esc-2115): raw INSERT로 잔액을 직접
    심지 않는다 — 실제 충전 진입점(`post_topup`)을 그대로 태우는
    `seed_user_available_balance`로 TOPUP_CONFIRMED 분개를 남긴다."""
    await seed_user_available_balance(pool, user_id, amount)


async def _available(pool, user_id: UUID) -> Decimal:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            ua(user_id, UserSub.AVAILABLE),
        )
    return value if value is not None else Decimal("0")


async def _create_strategy(pool, owner_user_id: UUID) -> tuple[str, str]:
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO strategies "
            "(strategy_id, version, owner_user_id, target_asset, market, exchange, "
            " fsm_definition, author_agent) "
            "VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', $4::jsonb, 'test-author')",
            strategy_id,
            version,
            owner_user_id,
            json.dumps({}),
        )
    return strategy_id, version


async def _always_eligible(strategy_id: str, version: str, seller_user_id: object = None) -> bool:
    return True


async def _listed_listing(pool, seller: UUID, price: Decimal | None):
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    verification_service = VerificationService(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    listing = await listing_service.create_listing(seller, strategy_id, version, price)
    submitted = await listing_service.submit_for_verification(listing.id, seller)
    verifier = await create_test_user(pool)
    return await verification_service.decide(submitted.id, verifier, "APPROVE")


async def test_place_and_capture_hold_creates_two_entries_and_settles(pool, ports):
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    price = Decimal("100.00")
    await _seed_available(pool, buyer, price)
    reference = f"test-purchase:{uuid4()}"

    async with pool.acquire() as conn, conn.transaction():
        hold = await place_hold(
            conn,
            buyer_id=buyer,
            amount=price,
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
        assert hold.state == HoldState.PENDING
        mid_balance = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            ua(buyer, UserSub.AVAILABLE),
        )
        assert mid_balance == Decimal("0.00")

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

    assert capture.hold.state == HoldState.CAPTURED
    assert capture.commission_amount == Decimal("15.00")
    assert capture.payout_amount == Decimal("85.00")

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref LIKE $1",
            f"hold:{hold.hold_id}%",
        )
    assert entry_count == 2  # HOLD_PLACED + HOLD_CAPTURED


async def test_capture_expired_hold_is_rejected(pool, ports):
    buyer = await create_test_user(pool)
    price = Decimal("10.00")
    await _seed_available(pool, buyer, price)
    reference = f"test-expired:{uuid4()}"
    past = _clock() - timedelta(minutes=1)

    async with pool.acquire() as conn, conn.transaction():
        hold = await place_hold(
            conn,
            buyer_id=buyer,
            amount=price,
            purpose=_TEST_PURPOSE,
            reference=reference,
            expires_at=past,
            actor_subject_id=buyer,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
            holds=ports.holds,
        )

        with pytest.raises(HoldExpiredError):
            await capture_hold(
                conn,
                hold,
                seller_id=await create_test_user(pool),
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
        # PENDING인 홀드는 release로 되돌릴 수 있다(구매가 아니어도 재사용, LC-14 대비).
        released = await release_hold(
            conn,
            hold,
            reason="test",
            actor_subject_id=buyer,
            trace_id=uuid4(),
            now=_clock(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
            holds=ports.holds,
        )
    assert released.state == HoldState.RELEASED
    assert await _available(pool, buyer) == price


async def test_place_hold_concurrent_same_reference_only_one_succeeds(pool, ports):
    buyer = await create_test_user(pool)
    price = Decimal("10.00")
    await _seed_available(pool, buyer, price * 5)
    reference = f"test-concurrent:{uuid4()}"
    expires_at = _clock() + timedelta(minutes=15)

    async def _attempt() -> HoldView:
        async with pool.acquire() as conn, conn.transaction():
            return await place_hold(
                conn,
                buyer_id=buyer,
                amount=price,
                purpose=_TEST_PURPOSE,
                reference=reference,
                expires_at=expires_at,
                actor_subject_id=buyer,
                trace_id=uuid4(),
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
                holds=ports.holds,
            )

    results = await asyncio.gather(*[_attempt() for _ in range(5)], return_exceptions=True)
    successes = [r for r in results if isinstance(r, HoldView)]
    conflicts = [r for r in results if isinstance(r, HoldConflictError)]

    assert len(successes) == 1
    assert len(conflicts) == 4
    async with pool.acquire() as conn:
        hold_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_hold WHERE purpose = $1 AND reference = $2",
            _TEST_PURPOSE,
            reference,
        )
    assert hold_count == 1
    assert await _available(pool, buyer) == price * 4  # 5건 펀딩 중 1건만 실제로 차감·확정


async def test_free_listing_purchase_posts_zero_journal_entries(pool):
    seller = await create_test_user(pool)
    listing = await _listed_listing(pool, seller, price=None)
    buyer = await create_test_user(pool)
    service = PurchaseService(pool)

    result = await service.purchase(buyer, listing.listing_id)

    assert result.status == "CONFIRMED"
    assert result.platform_commission_amount is None
    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref LIKE $1",
            f"purchase:{result.purchase_id}%",
        )
    assert entry_count == 0


async def test_purchase_service_settles_seller_and_house_via_hold_flow(pool):
    seller = await create_test_user(pool)
    listing = await _listed_listing(pool, seller, price=Decimal("100.00"))
    buyer = await create_test_user(pool)
    await _seed_available(pool, buyer, Decimal("100.00"))
    service = PurchaseService(pool)

    result = await service.purchase(buyer, listing.listing_id)

    assert result.platform_commission_amount == Decimal("15.0000")
    assert result.seller_payout_amount == Decimal("85.0000")
    assert await _available(pool, buyer) == Decimal("0.00")
    assert await _available(pool, seller) == Decimal("85.00")

    reference = f"purchase:{result.purchase_id}"
    async with pool.acquire() as conn:
        hold_row = await conn.fetchrow(
            "SELECT state, entry_id, settled_entry_id FROM ledger_hold WHERE reference = $1",
            reference,
        )
        settlement_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref LIKE $1", f"{reference}:%"
        )
    assert hold_row["state"] == "CAPTURED"
    assert hold_row["entry_id"] is not None
    assert hold_row["settled_entry_id"] is not None
    assert settlement_count == 2  # PAYOUT_RELEASE + commission MANUAL_ADJUSTMENT


async def test_capture_already_captured_hold_is_rejected(pool, ports):
    """DEEPEN(task-2960): negative 3번째 — `test_capture_expired_hold_is_rejected`가
    "PENDING, capture, 만료" 가드를 증명한다면 이 테스트는 "CAPTURED, capture"
    (전이표에 없는 조합, §4.5)를 증명한다 — 이미 캡처된 홀드를 다시 캡처하려는
    이중 캡처 시도를 `hold_state.py`의 FSM이 DB 접근 전에 즉시 거부한다."""
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    price = Decimal("20.00")
    await _seed_available(pool, buyer, price)
    reference = f"test-double-capture:{uuid4()}"
    expires_at = _clock() + timedelta(minutes=15)

    async with pool.acquire() as conn, conn.transaction():
        hold = await place_hold(
            conn,
            buyer_id=buyer,
            amount=price,
            purpose=_TEST_PURPOSE,
            reference=reference,
            expires_at=expires_at,
            actor_subject_id=buyer,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
            holds=ports.holds,
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

    async with pool.acquire() as conn:
        entry_count_before = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref LIKE $1",
            f"hold:{hold.hold_id}%",
        )

    with pytest.raises(IllegalHoldTransitionError):
        async with pool.acquire() as conn, conn.transaction():
            await capture_hold(
                conn,
                capture.hold,
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
    # 이중 캡처 거부는 순수 FSM 가드(§4.5, hold_state.py)라 DB 접근 전에 일어난다
    # — capture.hold(메모리 상 값)만이 아니라 DB를 직접 재조회해 원 캡처 그대로
    # 남고 분개도 추가되지 않았음을 증명한다(XREV: 인메모리 단언만으로는 FSM이
    # DB UPDATE 이후에 거부하는 회귀를 검출하지 못한다).
    assert capture.hold.state == HoldState.CAPTURED
    async with pool.acquire() as conn:
        hold_row = await conn.fetchrow(
            "SELECT state, settled_entry_id FROM ledger_hold WHERE hold_id = $1", hold.hold_id
        )
        entry_count_after = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref LIKE $1",
            f"hold:{hold.hold_id}%",
        )
    assert hold_row["state"] == "CAPTURED"
    assert hold_row["settled_entry_id"] == capture.entry.entry_id
    assert entry_count_after == entry_count_before == 2  # HOLD_PLACED + HOLD_CAPTURED만, 추가 없음


async def test_release_already_captured_hold_is_rejected(pool, ports):
    """DEEPEN(task-4918): negative 4번째 — `release_hold`도 `capture_hold`와
    같은 FSM 가드(§4.5)를 공유한다: CAPTURED 홀드를 release하려는 시도(전이표에
    없는 조합)를 DB 접근 전에 거부해야 한다. `capture_hold` 쪽 이중 캡처
    테스트(XREV 지적)와 같은 방식으로, in-memory 단언이 아니라 DB를 재조회해
    hold 행이 CAPTURED로 남고 HOLD_RELEASED 분개가 생기지 않았음을 증명한다."""
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    price = Decimal("30.00")
    await _seed_available(pool, buyer, price)
    reference = f"test-release-after-capture:{uuid4()}"
    expires_at = _clock() + timedelta(minutes=15)

    async with pool.acquire() as conn, conn.transaction():
        hold = await place_hold(
            conn,
            buyer_id=buyer,
            amount=price,
            purpose=_TEST_PURPOSE,
            reference=reference,
            expires_at=expires_at,
            actor_subject_id=buyer,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
            holds=ports.holds,
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

    with pytest.raises(IllegalHoldTransitionError):
        async with pool.acquire() as conn, conn.transaction():
            await release_hold(
                conn,
                capture.hold,
                reason="test-illegal-release",
                actor_subject_id=buyer,
                trace_id=uuid4(),
                now=_clock(),
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
                holds=ports.holds,
            )

    async with pool.acquire() as conn:
        hold_row = await conn.fetchrow(
            "SELECT state, settled_entry_id FROM ledger_hold WHERE hold_id = $1", hold.hold_id
        )
        release_entry_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref = $1",
            f"hold:{hold.hold_id}:release",
        )
    assert hold_row["state"] == "CAPTURED"
    assert hold_row["settled_entry_id"] == capture.entry.entry_id
    assert release_entry_count == 0
    assert await _available(pool, buyer) == Decimal("0.00")  # release가 잘못 되돌리지 않았다


@pytest.mark.perf
async def test_place_and_capture_hold_round_trip_under_budget(pool, ports):
    """DEEPEN(task-2960): 수치 성능 단언 — `place_hold`+`capture_hold` 왕복
    20회(각자 fresh reference, 순차 실행)가 절대시간 예산 내에 있음을
    증명한다. 실 DB 라운드트립(계정 보장 INSERT ON CONFLICT ×2, 분개
    INSERT×2, 잔액 UPDATE×2, 홀드 INSERT+conditional UPDATE)이라 단위테스트
    수준 마이크로초 예산은 의미가 없다 — 로컬 postgres 기준 넉넉한 절대
    예산으로 회귀(N+1 쿼리 등)만 잡는다."""
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    price = Decimal("1.00")
    iterations = 20
    budget_sec = 5.0
    await _seed_available(pool, buyer, price * iterations)

    start = time.perf_counter()
    for _ in range(iterations):
        reference = f"test-perf:{uuid4()}"
        async with pool.acquire() as conn, conn.transaction():
            hold = await place_hold(
                conn,
                buyer_id=buyer,
                amount=price,
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
            await capture_hold(
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
    elapsed = time.perf_counter() - start
    print(
        f"[LC-13 purchase_flow] {iterations} place+capture 왕복 {elapsed:.3f}s "
        f"(budget<{budget_sec}s, avg={elapsed / iterations * 1000:.1f}ms)"
    )
    assert elapsed < budget_sec, (
        f"{iterations}회 place+capture 왕복이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


# ---- 게이트 적색 재현 — HOLD 충돌이 트랜잭션 전체를 롤백함(부분 커밋 없음) --------


async def test_gate_red_conflicting_hold_rolls_back_whole_transaction(pool, ports):
    """DEEPEN(task-2960): 게이트 적색 재현 — 모듈 docstring(동시성 절)이
    주장하는 "HoldConflictError가 나면 그 시도의 분개(잔액 차감 포함)까지
    함께 취소된다"를 순차 재현으로 직접 증명한다: 같은 트랜잭션 안에서
    (a) 합법적인 새 reference로 `place_hold` 성공 (b) 그 뒤 이미 다른
    트랜잭션에서 커밋된 reference로 `place_hold` 재시도 -> `HoldConflictError`.
    트랜잭션 전체가 롤백돼 (a)의 홀드도, 그 차감도 커밋되지 않아야 한다 —
    아니라면(부분 커밋되면) buyer 잔액이 이중으로 차감된 채 남는
    실제 자금 누출이라 이 스위트가 반드시 RED가 돼야 하는 경로다."""
    buyer = await create_test_user(pool)
    price = Decimal("10.00")
    await _seed_available(pool, buyer, price * 2)
    existing_reference = f"test-existing:{uuid4()}"
    new_reference = f"test-new-in-same-tx:{uuid4()}"
    expires_at = _clock() + timedelta(minutes=15)

    async with pool.acquire() as conn, conn.transaction():
        await place_hold(
            conn,
            buyer_id=buyer,
            amount=price,
            purpose=_TEST_PURPOSE,
            reference=existing_reference,
            expires_at=expires_at,
            actor_subject_id=buyer,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
            holds=ports.holds,
        )
    balance_after_first = await _available(pool, buyer)
    assert balance_after_first == price

    with pytest.raises(HoldConflictError):
        async with pool.acquire() as conn, conn.transaction():
            await place_hold(
                conn,
                buyer_id=buyer,
                amount=price,
                purpose=_TEST_PURPOSE,
                reference=new_reference,
                expires_at=expires_at,
                actor_subject_id=buyer,
                trace_id=uuid4(),
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
                holds=ports.holds,
            )
            await place_hold(
                conn,
                buyer_id=buyer,
                amount=price,
                purpose=_TEST_PURPOSE,
                reference=existing_reference,
                expires_at=expires_at,
                actor_subject_id=buyer,
                trace_id=uuid4(),
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
                holds=ports.holds,
            )

    async with pool.acquire() as conn:
        leaked_hold = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_hold WHERE reference = $1",
            new_reference,
        )
    assert leaked_hold == 0, "게이트 위반 트랜잭션의 앞선 place_hold가 커밋되어 남았습니다"
    assert await _available(pool, buyer) == balance_after_first  # 추가 차감 없음(부분 커밋 없음)


async def test_capture_hold_concurrent_same_hold_only_one_settles(pool, ports):
    """DEEPEN(task-4920): D3 실패주입 + INVARIANTS.md I-10("구현됨 != 작동함")
    적대적 교차검증 — 지금까지의 negative 테스트는 전부 *순차* 재현(먼저
    캡처를 끝낸 뒤 그 결과를 다시 캡처)이라 `capture_hold`의 가드가 실제
    동시 DB 경합에서도 버티는지는 증명하지 않는다. 같은 PENDING 홀드를 5개
    트랜잭션이 asyncio.gather로 동시에 capture하면, `post_entry`(LC-9)의
    `balances.get_for_update` 행잠금이 이들을 buyer `HELD` 계정에서
    직렬화한다 — 두 번째부터는 이미 0으로 줄어든 `HELD` 잔액을 같은 금액만큼
    또 줄이려다 `allow_negative=False`(LIABILITY, chart_of_accounts.py) 가드에
    걸려 `InsufficientAvailableError`로 거부되거나, 그 잠금 경쟁을 통과해도
    `holds.transition`의 105번 표준 조건부 UPDATE(expected_state=PENDING)가
    `ConcurrencyConflictError`로 거부한다 — 둘 중 어느 경로든 실제 지급이
    두 번 나가는 걸 막는 fail-closed다. 이 가드가 배선만 되고 실제로
    작동하지 않는다면(I-10) 같은 대금이 판매자에게 중복 정산되는 실물 자금
    이중지급 사고가 된다."""
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    price = Decimal("50.00")
    await _seed_available(pool, buyer, price)
    reference = f"test-concurrent-capture:{uuid4()}"
    expires_at = _clock() + timedelta(minutes=15)

    async with pool.acquire() as conn, conn.transaction():
        hold = await place_hold(
            conn,
            buyer_id=buyer,
            amount=price,
            purpose=_TEST_PURPOSE,
            reference=reference,
            expires_at=expires_at,
            actor_subject_id=buyer,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
            holds=ports.holds,
        )

    async def _attempt_capture() -> CaptureResult:
        async with pool.acquire() as conn, conn.transaction():
            return await capture_hold(
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

    results = await asyncio.gather(*[_attempt_capture() for _ in range(5)], return_exceptions=True)
    successes = [r for r in results if isinstance(r, CaptureResult)]
    rejections = [
        r for r in results if isinstance(r, (ConcurrencyConflictError, InsufficientAvailableError))
    ]
    unexpected = [
        r
        for r in results
        if not isinstance(r, (CaptureResult, ConcurrencyConflictError, InsufficientAvailableError))
    ]
    assert unexpected == [], f"예상 밖 예외/결과가 나왔습니다: {unexpected!r}"
    assert len(successes) == 1, f"정확히 1건만 정산돼야 합니다: {results!r}"
    assert len(rejections) == 4

    async with pool.acquire() as conn:
        hold_row = await conn.fetchrow(
            "SELECT state, settled_entry_id FROM ledger_hold WHERE hold_id = $1", hold.hold_id
        )
        capture_entry_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref = $1",
            f"hold:{hold.hold_id}:capture",
        )
        seller_payout = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            ua(seller, UserSub.PENDING_PAYOUT),
        )
    assert hold_row["state"] == "CAPTURED"
    assert capture_entry_count == 1  # 5번 동시 시도 중 정산 분개는 정확히 1건 -- 이중 정산 없음
    assert seller_payout == Decimal("42.50")  # 85% 딱 한 번 -- 두 번 들어갔다면 85.00일 것


async def test_purchase_flow_replays_byte_identical(pool, ports):
    """DEEPEN(task-4920): D3 게이트-적색 대응 축 -- `scripts/replay_verify.py`
    (FA-15)가 이 리프의 분개도 실제로 감시 대상에 넣는지 증명한다.
    `place_hold`+`capture_hold`가 만든 두 분개(HOLD_PLACED, HOLD_CAPTURED)로
    바뀐 계정들이 `replay_verify.verify()`의 시간창(§ window)에 잡혀 현재
    `ledger_balance` 행과 바이트 단위로 일치해야 한다 -- 불일치가 있으면
    이 스위트가 새로 만든 분개 경로 자체가 FA-15 게이트를 RED로 만든다는
    뜻이라 그 자리에서 잡아야 한다(`tests/integration/eventstore/
    test_replay_verify.py`가 이미 이 체커 자체의 fail-closed 배선을
    증명하므로, 여기서는 "이 리프의 분개가 그 체커에 걸리는가"만 본다)."""
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    price = Decimal("40.00")
    await _seed_available(pool, buyer, price)
    reference = f"test-replay-verify:{uuid4()}"

    async with pool.acquire() as conn, conn.transaction():
        hold = await place_hold(
            conn,
            buyer_id=buyer,
            amount=price,
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
        await capture_hold(
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

    report = await replay_verify.verify(pool, as_of=_clock() + timedelta(minutes=1), hours=1)

    # buyer AVAILABLE, buyer HELD, seller PENDING_PAYOUT, PLATFORM:COMMISSION_REVENUE
    # 최소 4개 계정이 이 창에서 감시 대상에 잡혀야 한다(안 잡히면 아래 report.ok는
    # 그냥 "본 적 없어서 통과"인 거짓 양성이 된다).
    assert report.streams_checked >= 4, (
        f"이 리프가 건드린 계정이 replay_verify 감시창에 안 잡혔습니다(streams_checked="
        f"{report.streams_checked})"
    )
    assert report.ok, f"replay_verify 불일치: {report.mismatches!r}"
