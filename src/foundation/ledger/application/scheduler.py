"""LC-10 — 원장 무결성 스케줄러: 주기적으로 `verify_ledger_integrity`를 돈다.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§7, §9 LC-10.

`src/services/execution_loop/scheduler.py`와 같은 설계 원칙을 따른다 —
한 주기의 실패(예외)가 루프 자체를 죽이지 않고, 다음 주기에 다시
시도한다. 다만 이 스케줄러는 매 주기 실행 대상을 나열할 필요 없이
"원장 전체"라는 단일 작업만 반복한다는 점이 다르다.

기본 주기는 §7 "5분 주기 100% 성공"(Draft)을 그대로 쓴다. `RiskPolicy`에는
아직 이 값이 없어(execution_loop처럼 정책 파일에서 읽지 않고) 생성자
인자로 직접 받는다 — `main.py` 배선(백그라운드 태스크 등록)은 이 리프의
파일 목록에 없어 후속 리프로 남긴다.
"""
from __future__ import annotations

import asyncio
import logging

import asyncpg

from src.core.observability.metrics_registry import MetricsRegistry
from src.foundation.ledger.application.post_entry import AuditAppender
from src.foundation.ledger.application.verify_integrity import verify_ledger_integrity
from src.foundation.ledger.contracts.v1 import IntegrityReport
from src.foundation.ledger.ports.balance_repository import BalanceRepository
from src.foundation.ledger.ports.journal_repository import LedgerJournalRepository

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 300.0


class LedgerIntegrityScheduler:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        journal: LedgerJournalRepository,
        balances: BalanceRepository,
        audit: AuditAppender,
        registry: MetricsRegistry,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._pool = pool
        self._journal = journal
        self._balances = balances
        self._audit = audit
        self._registry = registry
        self.interval_seconds = interval_seconds

    async def run_once(self) -> IntegrityReport:
        """검증 한 주기. 위반이면 CRITICAL 로그 — 실제 동결·감사는
        `verify_ledger_integrity` 자신이 같은 트랜잭션에서 이미 처리했다."""
        report = await verify_ledger_integrity(
            journal=self._journal,
            balances=self._balances,
            audit=self._audit,
            pool=self._pool,
            registry=self._registry,
        )
        if not (report.chain_ok and report.zero_sum_ok and not report.drifts):
            logger.critical(
                "ledger_integrity: 무결성 위반 감지 — write_frozen=true 요청됨 "
                "(first_broken_seq=%s, zero_sum_ok=%s, drifts=%d건)",
                report.first_broken_seq,
                report.zero_sum_ok,
                len(report.drifts),
            )
        return report

    async def run_forever(self) -> None:
        """main.py 백그라운드 태스크 본체(배선은 후속 리프). 한 주기의 실패가
        루프를 죽이지 않는다 — execution_loop/scheduler.py와 동일 설계."""
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.run_once()
            except Exception:
                logger.exception("ledger_integrity: 이번 주기 전체 실패 — 다음 주기에 재시도")
