"""L4-14 — OMS outbox 디스패처 조립(디스패처만; inbox/reconcile 워커는 후속 리프).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C `wiring.py`, §9 L4-14.
task-1538 decision: 조립만 — 백그라운드 태스크 등록은 `src/services/
background_loops.py`(EO-04 주입 패턴, PM이 직렬화하는 `main.py`)가 한다.

task-1720(P1-A, 감사 2026-09-06) — 위 task-1538 decision이 실제로는 지켜지지
않아 `start_background_loops`가 이 모듈을 전혀 import하지 않았다(src 임포터
0). 디스패처가 조립만 되고 한 번도 태스크로 등록된 적이 없어 outbox 커맨드
경로 전체가 운영에서 죽어 있었다 — 이제 `background_loops.py`가
`build_outbox_dispatcher`를 호출하고 `OMS_DISPATCHER_FLAG`로 등록한다.

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

`build_outbox_dispatcher`는 repo 포트를 호출부가 넘기는 순수 조립 함수로
남긴다(테스트가 포트 대역을 그대로 주입할 수 있게). 운영 배선용
`start_outbox_dispatcher_task`만 Postgres 구체 클래스(L4-08,
`adapters/order_repository.py`·`adapters/outbox_repository.py`)를 직접
고른다 — `background_loops.py`(P6 300줄 캡)가 그 선택까지 떠안지 않도록
이 모듈이 대신 진다.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from uuid import uuid4

import asyncpg

from src.exchanges.bitget.private_ws_mixin import (
    PrivateWsInboxClient,
    subscribe_bitget_orders_to_inbox,
)
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.application.outbox_commands import AdapterResolver
from src.services.oms.application.outbox_dispatcher import (
    DEFAULT_LEASE_SEC,
    DEFAULT_POLL_INTERVAL_SEC,
    OutboxDispatcher,
)
from src.services.oms.ports.repository import OrderRepoPort, OutboxRepoPort
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate

logger = logging.getLogger(__name__)

OMS_DISPATCHER_FLAG = "AIOS_OMS_DISPATCHER_ENABLED"


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
    등록한다(`AIOS_OMS_DISPATCHER_ENABLED`, 통합테스트는 0 — TESTING.md 표)."""
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


def start_outbox_dispatcher_task(
    pool: asyncpg.Pool, resolve_adapter: AdapterResolver
) -> asyncio.Task[None] | None:
    """운영 배선(task-1720) — Postgres 레포로 `build_outbox_dispatcher`를 조립한
    뒤 `OMS_DISPATCHER_FLAG`로 게이팅해 태스크로 만든다. 인자 하나로 줄인
    이유는 `background_loops.py`(P6 300줄 캡)에서 호출 한 줄로 끝내기
    위해서다 — 플래그 판정(`flag_enabled`와 같은 "1"/"0" 관례)까지 이
    함수가 진다(다른 루프들은 호출부가 판정하지만, 여기는 예외)."""
    dispatcher = build_outbox_dispatcher(
        pool,
        resolve_adapter=resolve_adapter,
        outbox_repo=OutboxRepository(),
        order_repo=PostgresOrderRepository(),
    )
    if os.environ.get(OMS_DISPATCHER_FLAG, "1") == "0":
        logger.warning(
            "oms_dispatcher: %s=0 — OMS outbox 디스패처를 띄우지 않습니다.", OMS_DISPATCHER_FLAG
        )
        return None
    return asyncio.create_task(dispatcher.run_forever())


def start_bitget_private_ws_inbox_task(
    pool: asyncpg.Pool, adapter: PrivateWsInboxClient
) -> asyncio.Task[None]:
    """L4-20 — Bitget private `orders` 채널 구독 조립(태스크 등록만, 이
    함수를 실제로 호출해 `background_loops.py`에 태우는 배선은 이 리프
    범위 밖 — L4-14 dispatcher가 그랬듯 후속 리프가 담당한다, decision
    "wiring.py 수정은 구독 등록 1블록으로 제한"). `adapter`는
    `CredentialResolver.get_adapter(tenant_id, "bitget")`가 돌려주는
    인스턴스를 그대로 넘기면 된다(factory의 LIVE 차단을 그대로 통과)."""
    inbox = InboxProcessor(pool)
    return asyncio.create_task(subscribe_bitget_orders_to_inbox(adapter, inbox))
