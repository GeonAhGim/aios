"""FD-8 실행 루프 스케줄러 — RUNNING 상태의 PAPER 실행 전부를 주기적으로 tick.

Spec: 03_core_modules_v1.1.md(v1.2 서문 "실행 루프"), config/risk_policy.yaml
`execution_loop.interval_sec`, docs/FULL_AUDIT_2026-09-02.md §3·§11 3단계.

전수감사에서 확인된 가장 큰 배선 결함을 닫는다 — `run_execution_tick`은
완전히 구현돼 있었지만 운영 앱(`src/main.py`) 어디에서도 호출되지 않아
전략→포트폴리오→리스크→실행 파이프라인이 한 번도 돌지 않았다. 이 모듈이
main.py의 다른 백그라운드 루프(heartbeat/alert/risk_guard)와 같은 패턴으로
그 호출을 담당한다.

설계 원칙:
- 판단 엔진 4개(Strategy/Portfolio/Risk/Executor)는 스케줄러가 한 번만
  만들어 모든 실행이 공유한다. StrategyEngine의 이전-틱 캐시와
  ExecutionEquityTracker는 이미 execution_id로 키를 나눠 두고 있다.
- 실행 하나의 tick 실패(자격증명 해지, 거래소 오류, 동시성 충돌)는 그
  실행만 건너뛰고 나머지는 계속 진행한다. 루프 자체는 절대 죽지 않는다.
- LIVE 실행은 조회 대상에서 제외한다 — Executor가 어차피 하드 차단하지만
  매 틱마다 예외를 만들어 로그를 오염시킬 이유가 없다(ADR-2026-08-29-E).
- 동시 tick 수는 세마포어로 제한한다(거래소 rate limit·DB 풀 보호).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID

import asyncpg

from src.core.executor.executor import Executor
from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.portfolio.engine import PortfolioEngine
from src.core.risk.engine import RiskEngine
from src.core.strategy.engine import StrategyEngine
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.credential_resolver import CredentialNotFoundError
from src.services.execution_loop.equity_tracker import ExecutionEquityTracker
from src.services.execution_loop.tick import run_execution_tick
from src.services.order_service.submit import PublishFn

logger = logging.getLogger(__name__)

AdapterResolver = Callable[[UUID, str], Awaitable[ExchangeAdapter]]

DEFAULT_MAX_CONCURRENT_TICKS = 4


@dataclass
class TickReport:
    ticked: list[int] = field(default_factory=list)
    skipped_no_credential: list[int] = field(default_factory=list)
    failed: dict[int, str] = field(default_factory=dict)


class ExecutionLoopScheduler:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        resolve_adapter: AdapterResolver,
        policy: RiskPolicy,
        publish: PublishFn | None = None,
        max_concurrent_ticks: int = DEFAULT_MAX_CONCURRENT_TICKS,
        equity_tracker: ExecutionEquityTracker | None = None,
    ) -> None:
        self._pool = pool
        self._resolve_adapter = resolve_adapter
        self._policy = policy
        self._publish = publish
        self._semaphore = asyncio.Semaphore(max_concurrent_ticks)
        self._strategy_engine = StrategyEngine()
        self._portfolio_engine = PortfolioEngine()
        self._risk_engine = RiskEngine(policy)
        self._executor = Executor()
        self._equity_tracker = equity_tracker or ExecutionEquityTracker()

    @property
    def interval_seconds(self) -> float:
        return self._policy.execution_loop.interval_sec

    async def list_runnable(self) -> list[dict[str, object]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, user_id, exchange FROM strategy_executions "
                "WHERE status = 'RUNNING' AND mode = 'PAPER' ORDER BY id"
            )
        return [dict(row) for row in rows]

    async def tick_all_running(self) -> TickReport:
        report = TickReport()
        runnable = await self.list_runnable()
        await asyncio.gather(*(self._tick_one(row, report) for row in runnable))
        return report

    async def _tick_one(self, row: dict[str, object], report: TickReport) -> None:
        execution_id = int(row["id"])  # type: ignore[call-overload]
        user_id = row["user_id"]
        exchange = str(row["exchange"])
        assert isinstance(user_id, UUID)
        async with self._semaphore:
            try:
                adapter = await self._resolve_adapter(user_id, exchange)
            except CredentialNotFoundError:
                report.skipped_no_credential.append(execution_id)
                logger.warning(
                    "execution_loop: execution_id=%s 자격증명 없음 — 이번 틱 건너뜀", execution_id
                )
                return
            try:
                await run_execution_tick(
                    self._pool,
                    adapter,
                    execution_id,
                    strategy_engine=self._strategy_engine,
                    portfolio_engine=self._portfolio_engine,
                    risk_engine=self._risk_engine,
                    executor=self._executor,
                    equity_tracker=self._equity_tracker,
                    policy=self._policy,
                    publish=self._publish,
                )
            except Exception as exc:
                report.failed[execution_id] = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    "execution_loop: execution_id=%s 틱 실패 — 다음 주기에 재시도", execution_id
                )
                return
            report.ticked.append(execution_id)

    async def run_forever(self) -> None:
        """main.py 백그라운드 태스크 본체. 한 주기의 실패가 루프를 죽이지 않는다."""
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.tick_all_running()
            except Exception:
                logger.exception("execution_loop: 이번 주기 전체 실패 — 다음 주기에 재시도")
