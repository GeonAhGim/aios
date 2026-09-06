"""task-1720(P1-A) 통합테스트 — `background_loops.py`가 실제로 OMS outbox
디스패처를 태스크로 등록하는지.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C `wiring.py`, §9
L4-14. 감사 2026-09-06 P1 — `wiring.build_outbox_dispatcher`(조립)는 있었지만
`start_background_loops`가 한 번도 호출하지 않아(src 임포터 0)
outbox_dispatcher.py/submit_order.py 등 OMS 커맨드 경로 전체가 운영에서
죽어 있었다. 이 테스트는 실제 `order_command_outbox`/`orders`(공유
TEST_DATABASE_URL)에 대고 `start_background_loops`를 직접 호출해, 등록된
태스크가 실제로 행을 처리하는지와 플래그 off 시 경고 로그를 남기는지 둘 다
증명한다.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from src.core.event_bus.in_process import InProcessEventBus
from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.safety.metrics_collector import ApiCallTracker
from src.services.background_loops import start_background_loops
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.application.wiring import OMS_DISPATCHER_FLAG
from tests.integration.oms.conftest import create_test_user, insert_order
from tests.support.oms_outbox_fakes import ScriptedAdapter, submit_payload


class _StubCredentialResolver:
    """`credential_resolver.get_adapter`만 흉내 낸다 — 이 테스트는 자격증명
    해석 자체가 아니라 디스패처가 실제로 배선돼 도는지를 본다."""

    def __init__(self, adapter: ScriptedAdapter) -> None:
        self._adapter = adapter

    async def get_adapter(self, tenant_id: UUID, exchange: str) -> ScriptedAdapter:
        return self._adapter


async def _enqueue_submit(pool, user_id: UUID) -> tuple[UUID, str]:
    order_repo = PostgresOrderRepository()
    outbox_repo = OutboxRepository()
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="VALIDATED")
        view = await order_repo.get_for_update(conn, order_id)
        await outbox_repo.enqueue(
            conn,
            order_id=order_id,
            command_type="SUBMIT",
            payload=submit_payload(view),
            not_before=datetime.now(timezone.utc),
        )
    return order_id, view.client_order_id


async def test_start_background_loops_registers_oms_dispatcher_and_processes_row(
    pool, monkeypatch
) -> None:
    monkeypatch.setenv(OMS_DISPATCHER_FLAG, "1")
    monkeypatch.setenv("AIOS_EXECUTION_LOOP_ENABLED", "0")
    monkeypatch.setenv("AIOS_STARTUP_RECOVERY_ENABLED", "0")

    user_id = await create_test_user(pool)
    order_id, client_order_id = await _enqueue_submit(pool, user_id)
    adapter = ScriptedAdapter()

    loops = await start_background_loops(
        pool=pool,
        policy=load_risk_policy(),
        event_bus=InProcessEventBus(),
        credential_resolver=_StubCredentialResolver(adapter),
        api_tracker=ApiCallTracker(),
    )
    try:
        for _ in range(50):
            status = await pool.fetchval("SELECT status FROM orders WHERE order_id = $1", order_id)
            if status == "ACKNOWLEDGED":
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("디스패처가 outbox 행을 시한 안에 처리하지 않았다 — 배선 회귀")
    finally:
        await loops.stop()

    # claim_batch는 전역 큐라(tests/integration/oms/test_concurrent_dispatchers.py와
    # 동일 관례) 공유 TEST_DATABASE_URL에 다른 테스트가 남긴 PENDING 행도 같은
    # 라운드에 클레임될 수 있다 — 이 테스트가 만든 client_order_id로만 좁혀서 본다.
    assert adapter.calls.count(client_order_id) == 1  # 실제로 어댑터가 호출됐다
    outbox_state = await pool.fetchval(
        "SELECT state FROM order_command_outbox WHERE order_id = $1", order_id
    )
    assert outbox_state == "DONE"


async def test_flag_off_logs_warning_and_skips_dispatcher_task(pool, monkeypatch, caplog) -> None:
    """negative — 플래그 off면 태스크를 띄우지 않고 경고 로그만 남긴다."""
    monkeypatch.setenv(OMS_DISPATCHER_FLAG, "0")
    monkeypatch.setenv("AIOS_EXECUTION_LOOP_ENABLED", "0")
    monkeypatch.setenv("AIOS_STARTUP_RECOVERY_ENABLED", "0")

    with caplog.at_level(logging.WARNING):
        loops = await start_background_loops(
            pool=pool,
            policy=load_risk_policy(),
            event_bus=InProcessEventBus(),
            credential_resolver=_StubCredentialResolver(ScriptedAdapter()),
            api_tracker=ApiCallTracker(),
        )
    try:
        messages = [r.getMessage() for r in caplog.records]
        assert any(m.startswith("oms_dispatcher:") and OMS_DISPATCHER_FLAG in m for m in messages)
        # heartbeat/alert/risk_guard/safety 4개뿐 — execution_loop도 플래그 off,
        # oms_dispatcher도 플래그 off라 태스크가 추가되지 않았다.
        assert len(loops.tasks) == 4
    finally:
        await loops.stop()
