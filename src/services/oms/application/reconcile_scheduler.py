"""L4-24 — 3자 대사 주기 실행기.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C `reconcile_scheduler.py`,
§9 L4-24, §5.1(advisory lock, REC-004).

DoD (d) "새 상주 루프 프레임워크를 재발명하지 않는다" — `OutboxDispatcher.
run_forever`(L4-14)와 같은 "무한 sleep→tick, 한 주기 예외는 로그만 남기고
계속"이라는 이미 검증된 패턴을 그대로 따른다. 이 리프는 orders만 비교하는
`three_way_reconciler.reconcile_account`(그 모듈 docstring — 내부 잔고/포지션
원장 부재로 범위 축소)만 호출하므로 주문 주기(5분) 하나만 둔다. 잔고·포지션
15분 주기(§2-C 표)는 그 원장이 생긴 뒤 추가한다.

실제 백그라운드 태스크 등록(`background_loops.py`)은 이 리프 스콥 밖이다 —
`wiring.py`의 `start_bitget_private_ws_inbox_task`(L4-20)와 같은 이유
("wiring.py 변경은 등록용 조립 함수 하나로 한정" decision, 후속 리프가
main.py/background_loops.py에 실제로 태스크로 얹는다).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import asyncpg

from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.reconciliation.domain.models import MaterialityPolicy
from src.services.oms.application.three_way_reconciler import DEFAULT_POLICY, reconcile_account

logger = logging.getLogger(__name__)

DEFAULT_ORDER_WINDOW = timedelta(minutes=5)
DEFAULT_INTERVAL_SEC = 300.0  # §2-C "주문 5분" 주기


@dataclass(frozen=True)
class ReconcileTarget:
    tenant_id: UUID
    connection_id: UUID | None
    account_ref: str
    adapter: ExchangeAdapter


TargetProvider = Callable[[], Awaitable[Sequence[ReconcileTarget]]]


class ReconcileScheduler:
    """대상별 `pg_try_advisory_xact_lock(hashtext('recon:'||account_ref))` —
    같은 계정을 여러 스케줄러 인스턴스/수동 실행이 동시에 대사하지 않도록
    한다(REC-004 "충돌 시 skip"). 잠금은 그 대사 1회를 감싸는 트랜잭션이
    끝나면(advisory **xact** lock) 자동 해제된다 — 별도 해제 호출이 없다."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        targets: TargetProvider,
        window: timedelta = DEFAULT_ORDER_WINDOW,
        policy: MaterialityPolicy = DEFAULT_POLICY,
        interval_sec: float = DEFAULT_INTERVAL_SEC,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._pool = pool
        self._targets = targets
        self._window = window
        self._policy = policy
        self._interval_sec = interval_sec
        self._sleep = sleep

    async def tick(self) -> int:
        """한 주기 — 대상 전부를 대사 시도. 반환값은 실제로 락을 얻어 대사를
        수행한 건수(락 획득 실패로 건너뛴 건은 제외, 관측용)."""
        reconciled = 0
        for target in await self._targets():
            if await self._reconcile_one(target):
                reconciled += 1
        return reconciled

    async def _reconcile_one(self, target: ReconcileTarget) -> bool:
        async with self._pool.acquire() as conn, conn.transaction():
            acquired = await conn.fetchval(
                "SELECT pg_try_advisory_xact_lock(hashtext('recon:' || $1))", target.account_ref
            )
            if not acquired:
                logger.info(
                    "reconcile_scheduler: account_ref=%s 이미 대사 중 — 이번 주기 건너뜁니다"
                    "(REC-004).",
                    target.account_ref,
                )
                return False
            try:
                summary = await reconcile_account(
                    pool=self._pool,
                    adapter=target.adapter,
                    tenant_id=target.tenant_id,
                    connection_id=target.connection_id,
                    account_ref=target.account_ref,
                    window=self._window,
                    policy=self._policy,
                )
            except Exception:
                logger.exception(
                    "reconcile_scheduler: account_ref=%s 대사 실패 — 다음 주기에 재시도",
                    target.account_ref,
                )
                return False
            if summary.overall_classification != "HEALTHY":
                logger.warning(
                    "reconcile_scheduler: account_ref=%s classification=%s discrepancies=%d",
                    target.account_ref,
                    summary.overall_classification,
                    len(summary.discrepancies),
                )
        return True

    async def run_forever(self) -> None:
        """`outbox_dispatcher.run_forever`와 동일 원칙 — 한 주기 전체 실패가
        루프 자체를 죽이지 않는다."""
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("reconcile_scheduler: 이번 주기 전체 실패 — 다음 주기에 재시도")
            await self._sleep(self._interval_sec)
