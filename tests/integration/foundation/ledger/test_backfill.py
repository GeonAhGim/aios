"""LC-11 `backfill_ledger` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LC-11.
DoD: "픽스처 wallet_transactions(환불 포함) 백필 → Σ=0·잔액 전원 일치",
"불일치 1건이라도 있으면 전체 롤백(부분 적재 금지)", "재실행 멱등
(REPLAY, 중복 분개 0)".

`related_purchase_id`·`wallet_transactions.id`는 매 테스트 실행마다 랜덤
정수를 쓴다 — 이 DB는 세션 간 초기화되지 않는 영속 테스트 DB이므로
고정 정수를 쓰면 재실행 시 `idempotency_key`가 겹쳐 이전 실행의 다른
`buyer`/`seller`와 DIGEST_MISMATCH가 난다(`test_post_entry.py`와 같은
관례로 회피).

DEPTH 감사(task-2723, docs/audit/DEPTH_LA_LB_LC.md #349) 보강분: 아래
4가지를 이 파일 하단에 추가한다 — (1) failure-injection(리스트 중간
실패가 이미 성공한 앞선 이벤트까지 롤백시키는지), (2) 수치 round-trip
(커미션 rate 역산이 원래 값을 정확히 복원하는지) + 성능(이벤트당 DB
왕복 수 회귀 가드, 절대 ms 대신 왕복 수를 쓰는 이유는
`test_perf_journal.py`와 동일 — task-920/1029 전례), (3) 게이트 적색/
초록 재현(CLI `scripts/ledger_backfill.py`의 실제 exit code 계약),
(4) negative 3번째 축(형태는 완전하지만 내용이 모순인 중복 tx_type).
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from scripts import ledger_backfill as cli
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.backfill import (
    BackfillMismatchError,
    LegacyWalletTx,
    UnrecognizedTxGroupError,
    _rate_for,
    backfill_ledger,
)
from src.foundation.ledger.contracts.v1 import UserSub
from src.foundation.ledger.domain.chart_of_accounts import PLATFORM_REFUND_RESERVE
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.rounding import split_commission


def _clock() -> datetime:
    return datetime.now(timezone.utc)


class _IdSeq:
    """`wallet_transactions.id`(BIGSERIAL, 발생 순서) 대역 — 랜덤 시작점(재실행
    시 이전 실행 데이터와 충돌 방지) + 호출 순서대로 단조증가(백필이 이
    순서를 발생 순서로 신뢰하므로, 매 호출 독립 난수를 쓰면 순서가
    뒤섞여 중간 잔액이 음수로 떨어질 수 있다)."""

    def __init__(self) -> None:
        self._next = uuid4().int % 900_000_000 + 100_000_000

    def __call__(self) -> int:
        value = self._next
        self._next += 1
        return value


class _RealPorts:
    def __init__(self, pool):
        self.journal = PostgresJournalRepository(pool)
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


async def _account_balance(pool, code: str) -> Decimal:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            code,
        )
    return value if value is not None else Decimal("0")


def _purchase_rows(
    ids: _IdSeq,
    purchase_id: int,
    buyer: UUID,
    seller: UUID,
    *,
    price: Decimal,
    commission: Decimal,
) -> list[LegacyWalletTx]:
    payout = price - commission
    return [
        LegacyWalletTx(
            id=ids(),
            user_id=buyer,
            tx_type="PURCHASE_DEBIT",
            amount=-price,
            related_purchase_id=purchase_id,
        ),
        LegacyWalletTx(
            id=ids(),
            user_id=seller,
            tx_type="SALE_CREDIT",
            amount=payout,
            related_purchase_id=purchase_id,
        ),
        LegacyWalletTx(
            id=ids(),
            user_id=uuid4(),
            tx_type="COMMISSION_CREDIT",
            amount=commission,
            related_purchase_id=purchase_id,
        ),
    ]


def _refund_row(ids: _IdSeq, purchase_id: int, buyer: UUID, price: Decimal) -> LegacyWalletTx:
    return LegacyWalletTx(
        id=ids(),
        user_id=buyer,
        tx_type="REFUND",
        amount=price,
        related_purchase_id=purchase_id,
    )


def _clawback_rows(
    ids: _IdSeq,
    purchase_id: int,
    seller: UUID,
    *,
    seller_share: Decimal,
    commission_share: Decimal,
) -> list[LegacyWalletTx]:
    return [
        LegacyWalletTx(
            id=ids(),
            user_id=seller,
            tx_type="REFUND_SELLER_CLAWBACK",
            amount=-seller_share,
            related_purchase_id=purchase_id,
        ),
        LegacyWalletTx(
            id=ids(),
            user_id=uuid4(),
            tx_type="REFUND_COMMISSION_CLAWBACK",
            amount=-commission_share,
            related_purchase_id=purchase_id,
        ),
    ]


async def test_backfill_topup_purchase_and_refunds_match_expected_balances(pool, ports):
    buyer, seller = uuid4(), uuid4()
    reserve_before = await _account_balance(pool, PLATFORM_REFUND_RESERVE)

    ids = _IdSeq()
    purchase_a, purchase_b = ids(), ids()
    rows = [
        LegacyWalletTx(id=ids(), user_id=buyer, tx_type="TOPUP", amount=Decimal("1000.00")),
        *_purchase_rows(
            ids, purchase_a, buyer, seller, price=Decimal("700.00"), commission=Decimal("105.00")
        ),
        # 클로백 없음 = 구 자금창출형 환불(R1)
        _refund_row(ids, purchase_a, buyer, Decimal("700.00")),
        *_purchase_rows(
            ids, purchase_b, buyer, seller, price=Decimal("400.00"), commission=Decimal("60.00")
        ),
        _refund_row(ids, purchase_b, buyer, Decimal("400.00")),
        *_clawback_rows(
            ids,
            purchase_b,
            seller,
            seller_share=Decimal("340.00"),
            commission_share=Decimal("60.00"),
        ),
    ]
    # 기대 잔액: buyer = 1000(topup) -700+700(구매a+환불a) -400+400(구매b+환불b) = 1000
    #           seller = 595(구매a, 소급 회수 없음) + 340 - 340(구매b, 완전 클로백) = 595
    expected = {buyer: Decimal("1000.00"), seller: Decimal("595.00")}

    async with pool.acquire() as conn, conn.transaction():
        report = await backfill_ledger(
            conn,
            rows,
            expected,
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=_clock,
        )

    # TOPUP 1 + (HOLD_PLACED+CAPTURED+PAYOUT_RELEASE)×2구매
    # + MANUAL_ADJUSTMENT(환불a) + REFUND(환불b) = 1+6+1+1 = 9
    assert report.entries_posted == 9
    assert report.accounts_verified == 2

    async with pool.acquire() as conn, conn.transaction():
        balances = await ports.balances.get_for_update(
            conn, [ua(buyer, UserSub.AVAILABLE), ua(seller, UserSub.AVAILABLE)]
        )
    assert balances[ua(buyer, UserSub.AVAILABLE)].balance == Decimal("1000.00")
    assert balances[ua(seller, UserSub.AVAILABLE)].balance == Decimal("595.00")

    reserve_after = await _account_balance(pool, PLATFORM_REFUND_RESERVE)
    assert reserve_after - reserve_before == Decimal("700.00")  # 환불a의 창출분만 흡수


async def test_backfill_rolls_back_everything_on_balance_mismatch(pool, ports):
    buyer = uuid4()
    row_id = _IdSeq()()
    rows = [LegacyWalletTx(id=row_id, user_id=buyer, tx_type="TOPUP", amount=Decimal("50.00"))]
    wrong_expected = {buyer: Decimal("999.00")}

    with pytest.raises(BackfillMismatchError):
        async with pool.acquire() as conn, conn.transaction():
            await backfill_ledger(
                conn,
                rows,
                wrong_expected,
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=_clock,
            )

    async with pool.acquire() as conn:
        found = await conn.fetchval(
            "SELECT 1 FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"TOPUP_CONFIRMED:backfill:topup:{row_id}",
        )
        balance = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            ua(buyer, UserSub.AVAILABLE),
        )
    assert found is None  # 분개는 실제로 append됐었지만(post_entry는 성공) 트랜잭션째 사라짐
    assert balance is None  # 계정 생성(ensure_user_account)까지 포함해 전부 롤백


async def test_backfill_rejects_incomplete_purchase_group(pool, ports):
    seller = uuid4()
    ids = _IdSeq()
    rows = [
        LegacyWalletTx(
            id=ids(),
            user_id=seller,
            tx_type="SALE_CREDIT",
            amount=Decimal("100.00"),
            related_purchase_id=ids(),
        ),
    ]
    with pytest.raises(UnrecognizedTxGroupError):
        async with pool.acquire() as conn, conn.transaction():
            await backfill_ledger(
                conn,
                rows,
                {},
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=_clock,
            )


async def test_backfill_rejects_duplicate_tx_type_in_same_purchase_group(pool, ports):
    """negative 3번째 축: 불완전 세트(2건째)·잔액 불일치(1건째)와 달리, 이
    그룹은 형태상 "구매 세트"로 보이지만 같은 `related_purchase_id`에
    `SALE_CREDIT`이 두 번 들어 있다 — `_build_events`가 `by_type` dict로
    묶는 순간 하나가 조용히 덮여 잘못된 분개가 나갈 수 있으므로,
    `len(by_type) != len(group)`에서 fail-closed로 전체를 거부해야 한다."""
    buyer, seller = uuid4(), uuid4()
    ids = _IdSeq()
    purchase_id = ids()
    rows = [
        LegacyWalletTx(
            id=ids(),
            user_id=buyer,
            tx_type="PURCHASE_DEBIT",
            amount=Decimal("-100.00"),
            related_purchase_id=purchase_id,
        ),
        LegacyWalletTx(
            id=ids(),
            user_id=seller,
            tx_type="SALE_CREDIT",
            amount=Decimal("85.00"),
            related_purchase_id=purchase_id,
        ),
        LegacyWalletTx(
            id=ids(),
            user_id=seller,
            tx_type="SALE_CREDIT",
            amount=Decimal("85.00"),
            related_purchase_id=purchase_id,
        ),
    ]
    with pytest.raises(UnrecognizedTxGroupError):
        async with pool.acquire() as conn, conn.transaction():
            await backfill_ledger(
                conn,
                rows,
                {},
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=_clock,
            )


async def test_backfill_rerun_is_idempotent_replay_without_duplicate_entries(pool, ports):
    buyer = uuid4()
    row_id = _IdSeq()()
    rows = [LegacyWalletTx(id=row_id, user_id=buyer, tx_type="TOPUP", amount=Decimal("77.00"))]
    expected = {buyer: Decimal("77.00")}

    async with pool.acquire() as conn, conn.transaction():
        first = await backfill_ledger(
            conn,
            rows,
            expected,
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=_clock,
        )
    async with pool.acquire() as conn, conn.transaction():
        second = await backfill_ledger(
            conn,
            rows,
            expected,
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=_clock,
        )
    assert first.entries_posted == second.entries_posted == 1

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"TOPUP_CONFIRMED:backfill:topup:{row_id}",
        )
        balance = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            ua(buyer, UserSub.AVAILABLE),
        )
    assert count == 1
    assert balance == Decimal("77.00")


class _FlakyAuditAppender:
    """`fail_at`번째 `append_event_in` 호출에서만 실패를 주입한다 — 기존
    `test_backfill_rolls_back_everything_on_balance_mismatch`는 모든
    이벤트가 성공적으로 post_entry된 "이후" 최종 잔액 대조에서만
    실패하는 경로를 증명하지만, 실제 운영 중 실패(DB 커넥션 끊김·감사
    체인 장애 등)는 이벤트 리스트를 도는 도중 아무 때나 터질 수 있다.
    이 클래스는 그 중간 실패를 재현해 "이미 성공한 앞선 이벤트까지
    포함해 트랜잭션 전체가 롤백되는지"를 별도로 증명한다."""

    def __init__(self, real, fail_at: int) -> None:
        self._real = real
        self._fail_at = fail_at
        self._calls = 0

    async def append_event_in(self, conn, **kwargs):
        self._calls += 1
        if self._calls == self._fail_at:
            raise RuntimeError("injected audit failure mid-backfill")
        return await self._real.append_event_in(conn, **kwargs)


async def test_backfill_injected_failure_partway_through_events_rolls_back_earlier_ones_too(
    pool, ports
):
    buyer, other_user = uuid4(), uuid4()
    ids = _IdSeq()
    first_id = ids()
    second_id = ids()
    rows = [
        LegacyWalletTx(id=first_id, user_id=buyer, tx_type="TOPUP", amount=Decimal("42.00")),
        LegacyWalletTx(id=second_id, user_id=other_user, tx_type="TOPUP", amount=Decimal("10.00")),
    ]
    flaky_audit = _FlakyAuditAppender(
        ports.audit, fail_at=2
    )  # 1번째(TOPUP #1)는 성공, 2번째에서 폭발

    with pytest.raises(RuntimeError, match="injected audit failure"):
        async with pool.acquire() as conn, conn.transaction():
            await backfill_ledger(
                conn,
                rows,
                {},
                journal=ports.journal,
                balances=ports.balances,
                audit=flaky_audit,
                clock=_clock,
            )

    async with pool.acquire() as conn:
        first_entry = await conn.fetchval(
            "SELECT 1 FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"TOPUP_CONFIRMED:backfill:topup:{first_id}",
        )
        first_balance = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            ua(buyer, UserSub.AVAILABLE),
        )
    # 1번째 이벤트는 실패 시점에 이미 post_entry를 마쳤었지만(내부적으로 성공),
    # 2번째에서 터진 예외가 외부 트랜잭션 전체를 롤백시켜 함께 사라진다.
    assert first_entry is None
    assert first_balance is None


def test_commission_rate_round_trip_matches_original_split_for_sampled_prices() -> None:
    """`_rate_for`가 역산한 rate로 `split_commission`을 다시 돌리면, 모듈
    docstring(§9 LC-11)의 주장 "재양자화 후 원래 값과 정확히 같다(R3)"이
    실제로 성립해야 한다 — 아니면 HALF_EVEN 재양자화 과정에서 1원이라도
    어긋나는 legacy 행이 있다는 뜻이고, 그 오차는 `expected_balances`
    합계 대조로는 못 잡는다(총합은 맞아도 개별 커미션 배분이 다를 수
    있음). 결정론 시드로 고정해 실패를 항상 재현 가능하게 한다."""
    rng = random.Random(20260915)
    prices = [Decimal("0.01"), Decimal("0.02"), Decimal("100.00"), Decimal("999999.99")]
    prices += [Decimal(rng.randint(1, 100_000_000)) / 100 for _ in range(500)]
    rates = [Decimal("0.15"), Decimal("0.0"), Decimal("0.5"), Decimal("0.333"), Decimal("1")]

    for price in prices:
        for rate in rates:
            commission, payout = split_commission(price, rate)
            recovered_rate = _rate_for(commission, price)
            recovered_commission, recovered_payout = split_commission(price, recovered_rate)
            assert recovered_commission == commission
            assert recovered_payout == payout
            assert recovered_commission + recovered_payout == price


async def test_backfill_round_trip_count_stays_bounded_per_event(pool, ports):
    """수치 성능 가드 — 이벤트당 평균 DB 왕복 수가 이벤트 개수에 비례해서만
    커지면 되고(계정 ensure는 유니크 유저 수에만 비례하는 상수 오버헤드),
    `post_entry`의 LC-17 실측 상한(<=7/event, `test_perf_journal.py`) 위에
    backfill이 얹는 오버헤드가 이벤트 수에 비례해 커지면 회귀다. 절대
    ms 대신 왕복 수를 재는 이유는 이 저장소의 CI 절대지연 게이트 금지
    전례(task-920/1029)와 동일 — 환경 변동성 없이 구조 회귀만 잡는다."""
    buyer, seller = uuid4(), uuid4()
    ids = _IdSeq()
    n_groups = 20
    rows: list[LegacyWalletTx] = [
        LegacyWalletTx(id=ids(), user_id=buyer, tx_type="TOPUP", amount=Decimal("1000000.00")),
    ]
    for _ in range(n_groups):
        purchase_id = ids()
        rows += _purchase_rows(
            ids, purchase_id, buyer, seller, price=Decimal("10.00"), commission=Decimal("1.50")
        )

    queries: list[str] = []

    def _log(record: object) -> None:
        queries.append(getattr(record, "query", ""))

    async with pool.acquire() as conn:
        # 워밍업: 이 커넥션에서 커스텀 타입(감사 이벤트 enum/jsonb) 코덱을 처음
        # 알아내는 일회성 드라이버 비용을 먼저 흡수한다(test_perf_journal.py
        # `_count_append_round_trips`와 동일한 이유) — 실제 측정 대상은
        # `backfill_ledger` 자체의 이벤트당 반복 비용이지, 이 커넥션의
        # 최초 사용 비용이 아니다.
        warmup_rows = [
            LegacyWalletTx(id=ids(), user_id=uuid4(), tx_type="TOPUP", amount=Decimal("1.00"))
        ]
        async with conn.transaction():
            await backfill_ledger(
                conn,
                warmup_rows,
                {},
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=_clock,
            )

        conn.add_query_logger(_log)
        try:
            async with conn.transaction():
                await backfill_ledger(
                    conn,
                    rows,
                    {},
                    journal=ports.journal,
                    balances=ports.balances,
                    audit=ports.audit,
                    clock=_clock,
                )
        finally:
            conn.remove_query_logger(_log)

    event_count = 1 + n_groups * 3  # TOPUP_CONFIRMED + (HOLD_PLACED+CAPTURED+PAYOUT_RELEASE)×n
    per_event = len(queries) / event_count
    print(
        f"\nbackfill round trips (warmed connection): total={len(queries)} "
        f"events={event_count} per_event={per_event:.2f}"
    )
    # 실측 보정(HOLD_CAPTURED는 buyer+seller 2계정을 동시에 건드려 LC-17의
    # 단일계정 기준(<=7)보다 자연히 높다): 워밍업 커넥션에서 n_groups=20
    # 배치 실측 ≈17.95/event. 22.0은 그 위에 ~22% 여유를 둔 상한 —
    # 계정 ensure가 상수(유니크 유저 수 기준)에서 이벤트마다 재실행되는
    # 회귀로 퇴행하면(이 픽스처는 유니크 유저 2명 고정이라 반복 재실행 시
    # +8쿼리/event가 그대로 더해짐) 확실히 넘는 폭이다.
    assert per_event <= 22.0, (
        f"이벤트당 평균 DB 왕복 수({per_event:.2f})가 상한(22.0)을 넘었습니다 — "
        "post_entry 왕복 축소(LC-17, <=7/event, 단일계정 기준) 위에 backfill이 "
        "얹는 오버헤드(다계정 포스팅·계정 ensure)가 이벤트 수에 비례해 "
        "커지는 회귀입니다(예: 계정 ensure가 상수가 아니라 이벤트마다 "
        "재실행되도록 퇴행)."
    )


async def test_gate_red_cli_returns_1_and_rolls_back_on_balance_mismatch(
    pool, ports, monkeypatch, capsys
):
    """게이트 적색 재현 — `scripts/ledger_backfill.py`가 실제 CLI 진입점
    `_run()`으로 잔액 불일치 데이터를 만나면 exit code 1을 내고 아무것도
    커밋하지 않는지, `_load_rows`/`_load_expected_balances`만 격리
    픽스처로 교체해(공유 영속 DB의 실제 `wallet_transactions` 전체를
    스캔하지 않도록) 증명한다. 초록 대조는 아래
    `test_gate_green_cli_returns_0_on_clean_dry_run`."""
    buyer = uuid4()
    row_id = _IdSeq()()
    rows = [LegacyWalletTx(id=row_id, user_id=buyer, tx_type="TOPUP", amount=Decimal("50.00"))]
    wrong_expected = {buyer: Decimal("999.00")}

    async def _fake_load_rows(conn):
        return rows

    async def _fake_load_expected_balances(conn):
        return wrong_expected

    monkeypatch.setattr(cli, "_load_rows", _fake_load_rows)
    monkeypatch.setattr(cli, "_load_expected_balances", _fake_load_expected_balances)

    exit_code = await cli._run(apply=False)

    assert exit_code == 1
    assert "백필 실패" in capsys.readouterr().err

    async with pool.acquire() as conn:
        found = await conn.fetchval(
            "SELECT 1 FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"TOPUP_CONFIRMED:backfill:topup:{row_id}",
        )
    assert found is None


async def test_gate_green_cli_returns_0_on_clean_dry_run(pool, ports, monkeypatch, capsys):
    """게이트 초록 대조 — 잔액이 실제로 일치하는 데이터라면 `_run(apply=False)`는
    exit code 0을 내고(dry-run이므로) 여전히 아무것도 커밋하지 않는다."""
    buyer = uuid4()
    row_id = _IdSeq()()
    rows = [LegacyWalletTx(id=row_id, user_id=buyer, tx_type="TOPUP", amount=Decimal("321.00"))]
    expected = {buyer: Decimal("321.00")}

    async def _fake_load_rows(conn):
        return rows

    async def _fake_load_expected_balances(conn):
        return expected

    monkeypatch.setattr(cli, "_load_rows", _fake_load_rows)
    monkeypatch.setattr(cli, "_load_expected_balances", _fake_load_expected_balances)

    exit_code = await cli._run(apply=False)

    assert exit_code == 0
    assert "dry-run" in capsys.readouterr().out

    async with pool.acquire() as conn:
        found = await conn.fetchval(
            "SELECT 1 FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"TOPUP_CONFIRMED:backfill:topup:{row_id}",
        )
    assert found is None  # dry-run은 성공해도 커밋하지 않는다
