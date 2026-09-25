"""LC-12(a) `legacy_wallet_bridge` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§5.4(3단계), §9 LC-12.
DoD: "브리지 후 잔액 = ledger_balance 잔액", "wallet_transactions 행 계속
생성", 잔액 부족은 fail-closed(투영 미변경, `BridgeInsufficientBalanceError`).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID

import pytest

from src.foundation.ledger.adapters import legacy_wallet_bridge as bridge_module
from src.foundation.ledger.adapters.legacy_wallet_bridge import (
    BridgeInsufficientBalanceError,
    bridge_credit,
    bridge_debit,
)
from src.foundation.ledger.application.post_entry import LedgerWriteFrozenError
from src.foundation.ledger.contracts.v1 import UserSub
from src.foundation.ledger.domain.chart_of_accounts import PLATFORM_CASH_CLEARING
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from tests.integration.conftest import create_test_user


async def _ledger_balance(pool, user_id: UUID) -> Decimal:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            ua(user_id, UserSub.AVAILABLE),
        )
    return value if value is not None else Decimal("0")


async def _account_ledger_balance(pool, account_code: str) -> Decimal:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            account_code,
        )
    return value if value is not None else Decimal("0")


async def _wallet_balance(pool, user_id: UUID) -> Decimal:
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT balance FROM user_wallets WHERE user_id = $1", user_id)
    return value if value is not None else Decimal("0")


async def test_bridge_credit_matches_ledger_and_projects_wallet_transactions(pool):
    user = await create_test_user(pool)

    async with pool.acquire() as conn, conn.transaction():
        balance_after = await bridge_credit(conn, user, Decimal("300.00"), "TOPUP")

    assert balance_after == Decimal("300.00")
    assert await _wallet_balance(pool, user) == Decimal("300.00")
    assert await _ledger_balance(pool, user) == Decimal("300.00")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT tx_type, amount, balance_after, related_purchase_id "
            "FROM wallet_transactions WHERE user_id = $1",
            user,
        )
    assert row["tx_type"] == "TOPUP"
    assert row["amount"] == Decimal("300.00")
    assert row["balance_after"] == Decimal("300.00")
    assert row["related_purchase_id"] is None


async def test_bridge_debit_matches_ledger_after_credit(pool):
    user = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, user, Decimal("500.00"), "TOPUP")

    async with pool.acquire() as conn, conn.transaction():
        balance_after = await bridge_debit(conn, user, Decimal("120.00"), "PURCHASE_DEBIT")

    assert balance_after == Decimal("380.00")
    assert await _wallet_balance(pool, user) == Decimal("380.00")
    assert await _ledger_balance(pool, user) == Decimal("380.00")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT amount, related_purchase_id FROM wallet_transactions "
            "WHERE user_id = $1 AND tx_type = 'PURCHASE_DEBIT'",
            user,
        )
    assert row["amount"] == Decimal("-120.00")
    assert row["related_purchase_id"] is None


async def test_bridge_debit_insufficient_balance_rolls_back_without_projecting(pool):
    user = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, user, Decimal("50.00"), "TOPUP")

    with pytest.raises(BridgeInsufficientBalanceError):
        async with pool.acquire() as conn, conn.transaction():
            await bridge_debit(conn, user, Decimal("999.00"), "PURCHASE_DEBIT")

    # 실패한 시도가 잔액·투영을 조금도 건드리지 않았어야 한다(fail-closed).
    assert await _wallet_balance(pool, user) == Decimal("50.00")
    assert await _ledger_balance(pool, user) == Decimal("50.00")
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM wallet_transactions "
            "WHERE user_id = $1 AND tx_type = 'PURCHASE_DEBIT'",
            user,
        )
    assert count == 0


async def test_bridge_credit_creates_wallet_row_for_brand_new_user(pool):
    user = await create_test_user(pool)

    async with pool.acquire() as conn, conn.transaction():
        balance_after = await bridge_credit(conn, user, Decimal("10.00"), "SALE_CREDIT")

    assert balance_after == Decimal("10.00")
    assert await _wallet_balance(pool, user) == Decimal("10.00")
    assert await _ledger_balance(pool, user) == Decimal("10.00")


async def test_bridge_debit_fails_closed_when_ledger_frozen(pool):
    """negative #2 — `ledger_control.write_frozen=true`면 잔액이 충분해도
    `LedgerWriteFrozenError`로 거부되고 투영·원장·wallet_transactions
    무엇도 건드리지 않는다(fail-closed, §4.4)."""
    user = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, user, Decimal("100.00"), "TOPUP")

    async with pool.acquire() as conn:
        await conn.execute("UPDATE ledger_control SET write_frozen = TRUE WHERE id = 1")
    try:
        with pytest.raises(LedgerWriteFrozenError):
            async with pool.acquire() as conn, conn.transaction():
                await bridge_debit(conn, user, Decimal("40.00"), "PURCHASE_DEBIT")
    finally:
        async with pool.acquire() as conn:
            await conn.execute("UPDATE ledger_control SET write_frozen = FALSE WHERE id = 1")

    assert await _wallet_balance(pool, user) == Decimal("100.00")
    assert await _ledger_balance(pool, user) == Decimal("100.00")
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM wallet_transactions "
            "WHERE user_id = $1 AND tx_type = 'PURCHASE_DEBIT'",
            user,
        )
    assert count == 0


async def test_bridge_credit_fails_closed_when_ledger_frozen_for_brand_new_user(pool):
    """negative #3 — 신규 사용자(아직 `user_wallets` 행 없음)에 대한
    첫 크레딧도 동결 중엔 거부되며, 실패한 시도가 지갑 행조차 만들지
    않아야 한다(디버트 계정 신설 경로도 fail-closed)."""
    user = await create_test_user(pool)

    async with pool.acquire() as conn:
        await conn.execute("UPDATE ledger_control SET write_frozen = TRUE WHERE id = 1")
    try:
        with pytest.raises(LedgerWriteFrozenError):
            async with pool.acquire() as conn, conn.transaction():
                await bridge_credit(conn, user, Decimal("10.00"), "SALE_CREDIT")
    finally:
        async with pool.acquire() as conn:
            await conn.execute("UPDATE ledger_control SET write_frozen = FALSE WHERE id = 1")

    async with pool.acquire() as conn:
        wallet_row = await conn.fetchval("SELECT 1 FROM user_wallets WHERE user_id = $1", user)
    assert wallet_row is None
    assert await _ledger_balance(pool, user) == Decimal("0")


class _BoomAuditAppender:
    """실 감사 어댑터 결함(예: 제약조건 위반)을 흉내내는 대역 — LC-9 §6
    "C 감사 append 실패 → 포스팅 전체 롤백"을 브리지 경로에서도 증명한다."""

    async def append_event_in(self, conn, **kwargs):  # noqa: ANN001, ANN003, ARG002
        raise RuntimeError("injected audit adapter failure")


async def test_bridge_credit_rolls_back_entirely_when_audit_append_fails(pool, monkeypatch):
    """failure-injection — 감사 append가 실DB/어댑터 결함으로 실패하면
    분개·잔액·지갑 투영·wallet_transactions 전부 롤백돼야 한다."""
    user = await create_test_user(pool)
    monkeypatch.setattr(bridge_module, "_audit", _BoomAuditAppender())

    with pytest.raises(RuntimeError, match="injected audit adapter failure"):
        async with pool.acquire() as conn, conn.transaction():
            await bridge_credit(conn, user, Decimal("77.00"), "TOPUP")

    assert await _wallet_balance(pool, user) == Decimal("0")
    assert await _ledger_balance(pool, user) == Decimal("0")
    async with pool.acquire() as conn:
        wallet_row = await conn.fetchval("SELECT 1 FROM user_wallets WHERE user_id = $1", user)
        tx_count = await conn.fetchval(
            "SELECT count(*) FROM wallet_transactions WHERE user_id = $1", user
        )
    assert wallet_row is None
    assert tx_count == 0


class _QueryCountingConnection:
    """`bridge_credit`/`bridge_debit`에 실제 `asyncpg.Connection` 대신
    넘겨 SQL 왕복 횟수를 세는 얇은 프록시 — 브리지·`post_entry`·저장소
    전부 `self._pool`이 아니라 넘겨받은 `conn` 인자로만 쓰므로(모듈
    docstring 참고) 이 프록시 하나로 전 계층의 왕복을 다 잡는다."""

    _COUNTED = ("execute", "fetchval", "fetchrow", "fetch", "executemany")

    def __init__(self, real):  # noqa: ANN001
        self._real = real
        self.count = 0

    def __getattr__(self, name: str):
        attr = getattr(self._real, name)
        if name in self._COUNTED:

            async def _wrapped(*args, **kwargs):  # noqa: ANN002, ANN003
                self.count += 1
                return await attr(*args, **kwargs)

            return _wrapped
        return attr


async def test_bridge_credit_round_trip_count_stays_within_budget(pool):
    """수치 성능 단언 — 브리지 1회 호출(재동기화 4 + post_entry 7 + 투영
    갱신 2 안팎)이 SQL 왕복 수 상한을 넘지 않아야 한다. 절대값이 아니라
    N+1류 회귀를 잡는 상한만 단언해 구현 세부 변경에 과민하지 않게 한다."""
    user = await create_test_user(pool)
    async with pool.acquire() as real_conn, real_conn.transaction():
        counting = _QueryCountingConnection(real_conn)
        await bridge_credit(counting, user, Decimal("15.00"), "TOPUP")

    assert counting.count <= 30, f"unexpected round-trip blowup: {counting.count} queries"


async def test_bridge_bypass_breaks_projection_ledger_mirror_then_self_heals_gate_red(pool):
    """게이트 적색 재현 — 브리지를 우회한 직접 SQL(레거시 관리자 도구 흉내)이
    투영(`user_wallets`)과 원장(`ledger_balance`)을 실제로 갈라놓음을
    재현하고(적색), 다음 브리지 호출이 drift를 `PLATFORM:CASH_CLEARING`과의
    짝으로 흡수해 자가치유하면서 Σ=0 복식부기 불변식을 수치로 지킴을
    증명한다(모듈 docstring의 CASH_CLEARING 미러 논증)."""
    user = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, user, Decimal("200.00"), "TOPUP")

    cash_before = await _account_ledger_balance(pool, PLATFORM_CASH_CLEARING)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_wallets SET balance = balance + $2 WHERE user_id = $1",
            user,
            Decimal("50.00"),
        )

    # 적색 재현: 브리지를 거치지 않은 직접 SQL 직후, 투영과 원장이 갈라졌다.
    assert await _wallet_balance(pool, user) == Decimal("250.00")
    assert await _ledger_balance(pool, user) == Decimal("200.00")

    async with pool.acquire() as conn, conn.transaction():
        balance_after = await bridge_debit(conn, user, Decimal("30.00"), "PURCHASE_DEBIT")

    # 자가치유: 다음 브리지 호출이 drift(50.00)를 먼저 흡수한 뒤 정상
    # debit(30.00)을 적용해 투영·원장이 다시 정확히 일치한다.
    assert balance_after == Decimal("220.00")
    assert await _wallet_balance(pool, user) == Decimal("220.00")
    assert await _ledger_balance(pool, user) == Decimal("220.00")

    cash_after = await _account_ledger_balance(pool, PLATFORM_CASH_CLEARING)
    # CASH_CLEARING(ASSET)·사용자 계정(LIABILITY) 모두 drift 흡수(+50, 둘 다
    # 대차 짝으로 동시 증가) 다음 정상 debit 미러(-30, 둘 다 동시 감소)를
    # 거친다 — 매 분개가 차변=대변으로 이미 균형이라(balance_rules.check_balanced),
    # 두 계정의 누적 순변화가 우연이 아니라 항상 정확히 같아야 한다(+20=+20).
    assert cash_after - cash_before == Decimal("20.00")


async def test_bridge_concurrent_debits_race_never_overdraws_D3(pool):
    """D3 동시성/적대적 증명 — 잔액 100에 34원 인출 3건을 동시에 쏘면
    `get_for_update`의 advisory xact lock(LC-8b)이 직렬화해 정확히 2건만
    성공(68<=100)하고 3번째는 fail-closed로 거부되며, 잔액이 절대 음수가
    되지 않고 wallet_transactions는 성공한 만큼만 남는다."""
    user = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, user, Decimal("100.00"), "TOPUP")

    async def _attempt():
        async with pool.acquire() as conn, conn.transaction():
            return await bridge_debit(conn, user, Decimal("34.00"), "PURCHASE_DEBIT")

    results = await asyncio.gather(*(_attempt() for _ in range(3)), return_exceptions=True)

    successes = [r for r in results if isinstance(r, Decimal)]
    failures = [r for r in results if isinstance(r, BridgeInsufficientBalanceError)]
    assert len(successes) == 2
    assert len(failures) == 1

    final_wallet = await _wallet_balance(pool, user)
    final_ledger = await _ledger_balance(pool, user)
    assert final_wallet == Decimal("32.00")
    assert final_wallet == final_ledger
    assert final_wallet >= 0

    async with pool.acquire() as conn:
        tx_count = await conn.fetchval(
            "SELECT count(*) FROM wallet_transactions "
            "WHERE user_id = $1 AND tx_type = 'PURCHASE_DEBIT'",
            user,
        )
    assert tx_count == 2
