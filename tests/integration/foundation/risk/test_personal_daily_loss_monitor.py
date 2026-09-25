"""task-6510 (U-15, XREV task-3820 REJECT 수정) 통합테스트 — 상시 호출자가
실제 NAV 체인(`pos_nav_daily`, 실 DB)에서 읽은 값으로 3% 일일 손실 kill을
실제로 발동시키는지.

DoD(2): >3% 일일 손실 입력을 실제 호출 경로(`run_personal_daily_loss_monitor_
tick` -> `compute_personal_daily_loss_input` -> `check_personal_daily_loss`)로
주입해 kill이 발동함을 증명한다 — 호출자 자신(`run_personal_daily_loss_
monitor_tick`)은 모킹하지 않는다. `PersonalOperationStatePort`/
`PersonalNotifierPort`만 이 코드베이스의 기존 관례(adversarial 테스트와 동일)
대로 fake를 쓴다 — 이 둘은 이 leaf가 아니라 task-3986이 이미 owns한 포트다.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from src.data.models.base import Currency
from src.foundation.positions.adapters.postgres_nav_repository import PostgresNavRepository
from src.foundation.positions.contracts.v1 import NAVSnapshot
from src.foundation.risk.application.personal_daily_loss_monitor import (
    run_personal_daily_loss_monitor_tick,
)
from src.foundation.risk.application.personal_risk_snapshot_source import (
    compute_personal_daily_loss_input,
)
from src.foundation.risk.ports.notifier import NotifyResult, PersonalNotification
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import create_pos_account

_NAV_DATE = date(2026, 6, 1)
_MOMENT = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


class _FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[PersonalNotification] = []

    async def send(self, notification: PersonalNotification) -> NotifyResult:
        self.sent.append(notification)
        return NotifyResult(ok=True, status_code=200, error=None)


class _FakeState:
    def __init__(self, *, scoped_account_id: UUID | None) -> None:
        self._scoped_account_id = scoped_account_id
        self.kill_engaged = False
        self.kill_reason_value: str | None = None
        self.engage_calls = 0

    async def personal_mode_account_id(self) -> UUID | None:
        return self._scoped_account_id

    async def is_kill_engaged(self) -> bool:
        return self.kill_engaged

    async def kill_reason(self) -> str | None:
        return self.kill_reason_value

    async def engage_kill(self, *, reason: str) -> None:
        self.engage_calls += 1
        self.kill_engaged = True
        self.kill_reason_value = reason


def _nav(
    *, account_id: UUID, opening_nav: Decimal, realized: Decimal, closing_nav: Decimal
) -> NAVSnapshot:
    return NAVSnapshot(
        account_id=account_id,
        nav_date=_NAV_DATE,
        base_currency=Currency.KRW,
        opening_nav=opening_nav,
        cash=closing_nav,
        positions_mv=Decimal("0"),
        realized=realized,
        unrealized_delta=Decimal("0"),
        funding=Decimal("0"),
        fees=Decimal("0"),
        flows=realized,
        closing_nav=closing_nav,
        fx_rates=[],
        source_hash=hashlib.sha256(f"{account_id}-{closing_nav}".encode()).hexdigest(),
    )


async def _seed_account_with_nav(
    pool, *, opening_nav: Decimal, realized: Decimal, closing_nav: Decimal
) -> tuple[UUID, UUID]:
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    repo = PostgresNavRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        await repo.insert(
            conn,
            _nav(
                account_id=account_id,
                opening_nav=opening_nav,
                realized=realized,
                closing_nav=closing_nav,
            ),
        )
    return tenant_id, account_id


async def test_daily_loss_over_3pct_fires_kill_through_real_call_path(pool):
    """DoD(2) — 실현손실이 opening NAV 대비 -5%(> 3% 한도)인 실 NAV 행을
    심고, 모킹 없는 실제 호출 경로로 kill이 발동함을 증명한다."""
    tenant_id, account_id = await _seed_account_with_nav(
        pool,
        opening_nav=Decimal("1000000"),
        realized=Decimal("-50000"),
        closing_nav=Decimal("950000"),
    )
    state = _FakeState(scoped_account_id=tenant_id)
    notifier = _FakeNotifier()

    engaged = await run_personal_daily_loss_monitor_tick(
        pool,
        personal_state=state,
        personal_notifier=notifier,
        nav_repo=PostgresNavRepository(pool),
        now=_MOMENT,
    )

    assert engaged is True
    assert state.kill_engaged is True
    assert state.kill_reason_value == "DAILY_LOSS_LIMIT_BREACHED"
    kill_notifications = [n for n in notifier.sent if n.kind.value == "KILL_SWITCH"]
    assert len(kill_notifications) == 1


async def test_daily_loss_under_3pct_does_not_fire_kill(pool):
    """negative — -1% 손실(한도 미만)은 kill을 발동시키지 않는다."""
    tenant_id, account_id = await _seed_account_with_nav(
        pool,
        opening_nav=Decimal("1000000"),
        realized=Decimal("-10000"),
        closing_nav=Decimal("990000"),
    )
    state = _FakeState(scoped_account_id=tenant_id)
    notifier = _FakeNotifier()

    engaged = await run_personal_daily_loss_monitor_tick(
        pool,
        personal_state=state,
        personal_notifier=notifier,
        nav_repo=PostgresNavRepository(pool),
        now=_MOMENT,
    )

    assert engaged is False
    assert state.kill_engaged is False
    assert notifier.sent == []


async def test_no_nav_row_yet_is_a_no_op_not_a_false_kill(pool):
    """negative — LA-18(PositionsScheduler 미배선) 갭 상태를 흉내낸다: NAV
    행이 아직 없는 tenant는 이번 tick에서 아무 것도 건드리지 않는다(추측
    kill 금지)."""
    tenant_id = await create_test_tenant(pool)
    await create_pos_account(pool, tenant_id)
    state = _FakeState(scoped_account_id=tenant_id)
    notifier = _FakeNotifier()

    engaged = await run_personal_daily_loss_monitor_tick(
        pool,
        personal_state=state,
        personal_notifier=notifier,
        nav_repo=PostgresNavRepository(pool),
        now=_MOMENT,
    )

    assert engaged is False
    assert state.kill_engaged is False
    assert notifier.sent == []


async def test_personal_mode_not_scoped_is_a_no_op(pool):
    """negative — personal_mode_account_id()가 None이면(개인 모드 미설정)
    DB를 조회조차 하지 않고 바로 no-op한다(per-account opt-in 준수)."""
    state = _FakeState(scoped_account_id=None)
    notifier = _FakeNotifier()

    engaged = await run_personal_daily_loss_monitor_tick(
        pool,
        personal_state=state,
        personal_notifier=notifier,
        nav_repo=PostgresNavRepository(pool),
        now=_MOMENT,
    )

    assert engaged is False
    assert notifier.sent == []


async def test_compute_daily_loss_input_returns_none_for_tenant_without_accounts(pool):
    """negative — pos_account가 하나도 없는 tenant는 None(추측 금지)."""
    tenant_id = await create_test_tenant(pool)

    result = await compute_personal_daily_loss_input(
        pool, tenant_id, _NAV_DATE, nav_repo=PostgresNavRepository(pool)
    )

    assert result is None


async def test_compute_daily_loss_input_aggregates_across_multiple_accounts(pool):
    """양수 케이스 — tenant가 계좌 2개를 갖고 있으면 NAV를 합산한다."""
    tenant_id = await create_test_tenant(pool)
    account_a = await create_pos_account(pool, tenant_id)
    account_b = await create_pos_account(pool, tenant_id)
    repo = PostgresNavRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        await repo.insert(
            conn,
            _nav(
                account_id=account_a,
                opening_nav=Decimal("600000"),
                realized=Decimal("-30000"),
                closing_nav=Decimal("570000"),
            ),
        )
        await repo.insert(
            conn,
            _nav(
                account_id=account_b,
                opening_nav=Decimal("400000"),
                realized=Decimal("-20000"),
                closing_nav=Decimal("380000"),
            ),
        )

    result = await compute_personal_daily_loss_input(
        pool, tenant_id, _NAV_DATE, nav_repo=repo
    )

    assert result is not None
    assert result.account_equity_krw == Decimal("950000")
    assert result.daily_realized_pnl_pct == Decimal("-50000") / Decimal("1000000")


async def test_monitor_tick_survives_db_disconnect_during_nav_lookup(pool, monkeypatch):
    """실패주입 — NAV 조회 중 DB 커넥션이 끊기면 예외가 그대로 전파돼야
    한다(fail-closed: 이번 tick을 조용히 성공으로 착각해 kill 여부를
    잘못 판단하지 않는다). 상위 `run_periodic_loop`의 `_run_instrumented`가
    이 예외를 잡아 다음 주기에 재시도한다."""
    import asyncpg

    tenant_id, account_id = await _seed_account_with_nav(
        pool,
        opening_nav=Decimal("1000000"),
        realized=Decimal("-50000"),
        closing_nav=Decimal("950000"),
    )
    state = _FakeState(scoped_account_id=tenant_id)
    notifier = _FakeNotifier()

    async def _fake_fetch(*args, **kwargs):
        raise asyncpg.exceptions.ConnectionDoesNotExistError(
            {"file": b"conn.c", "line": b"123", "message": "connection does not exist"}
        )

    monkeypatch.setattr(asyncpg.Connection, "fetch", _fake_fetch)

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await run_personal_daily_loss_monitor_tick(
            pool,
            personal_state=state,
            personal_notifier=notifier,
            nav_repo=PostgresNavRepository(pool),
            now=_MOMENT,
        )
    assert state.kill_engaged is False
