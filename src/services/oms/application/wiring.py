"""L4-14 — OMS outbox 디스패처 조립(디스패처만; inbox/reconcile 워커는 후속 리프).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C `wiring.py`, §9 L4-14.
task-1538 decision: 조립만 — 백그라운드 태스크 등록은 `src/services/
background_loops.py`(EO-04 주입 패턴, PM이 직렬화하는 `main.py`)가 한다.

이 모듈이 고정하는 것(I-10 "배선·우회불가·증명됨"):
- 전송 게이트는 `make_foundation_pre_submit_gate(pool, require_mandate=False)` —
  실행 루프(`background_loops.py`)와 **같은 구현체**. kill switch·safety
  control(GLOBAL/TENANT/ACCOUNT/PROVIDER/STRATEGY_DEPLOYMENT)이 enqueue와
  dispatch 사이에 켜지면 전송을 막는다(우회 경로 없음, `OutboxDispatcher`는
  게이트를 Optional로 받지 않는다 — I-01).
- `worker_id`는 EO-02/04 실행 소유권과 같은 형식(`host:pid:uuid`) — outbox
  `worker_id`/`lease_until`이 곧 이 워커의 리스다(§5.1).
- 어댑터는 `CredentialResolver.get_adapter(tenant_id, exchange)`로만 얻는다 —
  factory의 LIVE 차단(§4.3, L4-13)을 그대로 통과한다. 이 모듈은 어댑터를
  직접 만들지 않는다.

repo 포트(`outbox_repo`/`order_repo`)는 호출부가 넘긴다 — Postgres 어댑터
(L4-08)가 아직 없어 여기서 구체 클래스를 import하면 배선이 거짓이 된다.
"""
from __future__ import annotations

import os
import socket
from uuid import uuid4

import asyncpg

from src.services.oms.application.outbox_commands import AdapterResolver
from src.services.oms.application.outbox_dispatcher import (
    DEFAULT_LEASE_SEC,
    DEFAULT_POLL_INTERVAL_SEC,
    OutboxDispatcher,
)
from src.services.oms.ports.repository import OrderRepoPort, OutboxRepoPort
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate

OUTBOX_DISPATCHER_FLAG = "AIOS_OUTBOX_DISPATCHER_ENABLED"


def default_worker_id() -> str:
    """EO-03/04 `owner_id`와 동일 형식 — 프로세스 재시작마다 새 값."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"


def build_outbox_dispatcher(
    pool: asyncpg.Pool,
    *,
    resolve_adapter: AdapterResolver,
    outbox_repo: OutboxRepoPort,
    order_repo: OrderRepoPort,
    worker_id: str | None = None,
    lease_sec: int = DEFAULT_LEASE_SEC,
    poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
) -> OutboxDispatcher:
    """디스패처 1개 조립. 반환값의 `run_forever()`를 `background_loops`가 태스크로
    등록한다(`AIOS_OUTBOX_DISPATCHER_ENABLED`, 통합테스트는 0 — TESTING.md 표)."""
    return OutboxDispatcher(
        pool,
        outbox_repo=outbox_repo,
        order_repo=order_repo,
        resolve_adapter=resolve_adapter,
        pre_send_gate=make_foundation_pre_submit_gate(pool, require_mandate=False),
        worker_id=worker_id if worker_id is not None else default_worker_id(),
        lease_sec=lease_sec,
        poll_interval_sec=poll_interval_sec,
    )
