"""LC-10/LC-16 — 원장 스케줄러: 무결성 검증(5분 주기) + 정산 배치(일 1회 00:10 KST).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§7, §9 LC-10, LC-16.

`src/services/execution_loop/scheduler.py`와 같은 설계 원칙을 따른다 —
한 주기의 실패(예외)가 루프 자체를 죽이지 않고, 다음 주기에 다시
시도한다.

기본 주기는 §7 "5분 주기 100% 성공"(Draft)을 그대로 쓴다. `RiskPolicy`에는
아직 이 값이 없어(execution_loop처럼 정책 파일에서 읽지 않고) 생성자
인자로 직접 받는다.

정산 부분(LC-16)은 `application/payouts.py::schedule_payouts`(LC-15a)를
매일 00:10 KST(§7, Draft)에 한 번 호출하는 배선만 한다 — 창 길이(§10 R2
Draft 7일)·전이 규칙은 재구현하지 않는다. 정산 후보(`CaptureRecord`) 조회는
`payouts.py` docstring이 "호출자 책임"으로 남긴 부분이라 여기서 SQL로
채운다: `HOLD_CAPTURED` 분개 중 아직 `ledger_payout_item`에 없는(=어느
배치에도 편입되지 않은) 판매자 `PENDING_PAYOUT` CREDIT 행 전부를 후보로
삼는다(기간과 무관하게 넓게 모은다 — `schedule_payouts`가 내부에서
`period_start ≤ captured_at.date() < period_end` + 정산창 경과만 실제로
골라 쓰므로, 여기서 기간을 좁게 잡으면 스케줄러가 하루 이상 멈췄던 동안의
백로그가 그 다음 실행에서도 영영 후보에서 빠지는 문제가 생긴다). 매 실행의
`period_end`는 그날의 KST 날짜, `period_start`는 사실상 무제한 과거로 잡아
백로그를 안전하게 흡수하고, 이미 배치에 편입된 캡처는 SQL의
`NOT EXISTS`가 걸러내므로 이중 지급 위험은 없다(§4.4 `ledger_payout_item.
capture_entry_id UNIQUE`와 별개의 독립된 방어선).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import asyncpg

from src.core.observability.metrics_registry import MetricsRegistry
from src.data.models.base import Currency
from src.foundation.ledger.application.payouts import DEFAULT_SETTLEMENT_WINDOW, schedule_payouts
from src.foundation.ledger.application.post_entry import AuditAppender, Clock
from src.foundation.ledger.application.verify_integrity import verify_ledger_integrity
from src.foundation.ledger.contracts.v1 import IntegrityReport, PayoutBatchView
from src.foundation.ledger.domain.chart_of_accounts import parse_account_code
from src.foundation.ledger.domain.payout_schedule import CaptureRecord
from src.foundation.ledger.ports.balance_repository import BalanceRepository
from src.foundation.ledger.ports.journal_repository import LedgerJournalRepository
from src.foundation.ledger.ports.payout_repository import PayoutRepository

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 300.0

_KST = ZoneInfo("Asia/Seoul")
DEFAULT_PAYOUT_RUN_TIME = time(0, 10)
# 스케줄러가 하루 이상 멈췄던 백로그까지 흡수하는 사실상 무제한 조회
# 창(§9 LC-16 DoD) — `NOT EXISTS`가 실제 중복 지급을 막으므로 넓게 잡아도
# 안전하다.
_PAYOUT_CAPTURE_LOOKBACK = timedelta(days=3650)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _seconds_until_next_run(now: datetime, run_time: time) -> float:
    now_kst = now.astimezone(_KST)
    candidate = datetime.combine(now_kst.date(), run_time, tzinfo=_KST)
    if candidate <= now_kst:
        candidate += timedelta(days=1)
    return (candidate - now_kst).total_seconds()


class LedgerIntegrityScheduler:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        journal: LedgerJournalRepository,
        balances: BalanceRepository,
        audit: AuditAppender,
        registry: MetricsRegistry,
        payouts: PayoutRepository,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        settlement_window: timedelta = DEFAULT_SETTLEMENT_WINDOW,
        payout_run_time: time = DEFAULT_PAYOUT_RUN_TIME,
        clock: Clock = _utcnow,
    ) -> None:
        self._pool = pool
        self._journal = journal
        self._balances = balances
        self._audit = audit
        self._registry = registry
        self._payouts = payouts
        self.interval_seconds = interval_seconds
        self._settlement_window = settlement_window
        self._payout_run_time = payout_run_time
        self._clock: Callable[[], datetime] = clock

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
        """main.py 백그라운드 태스크 본체(무결성 검증 루프). 한 주기의 실패가
        루프를 죽이지 않는다 — execution_loop/scheduler.py와 동일 설계."""
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.run_once()
            except Exception:
                logger.exception("ledger_integrity: 이번 주기 전체 실패 — 다음 주기에 재시도")

    async def _fetch_payout_capture_candidates(
        self, conn: asyncpg.Connection
    ) -> list[CaptureRecord]:
        """`ledger_payout_item`에 아직 없는 `HOLD_CAPTURED` 캡처를 후보로
        모은다. 딱 하나 더 걸러낸다 — `purchase_service.py::_settle`(Phase 1
        "판매대금 즉시 정산", §10 R2 ADR 개정 전 임시)은 캡처 직후 같은
        트랜잭션에서 `payouts.py`를 거치지 않고 직접 `PAYOUT_RELEASE`를
        포스팅해 `ledger_payout_item`을 전혀 남기지 않는다 — 그래서 그
        경로로 이미 정산된 캡처가 위 `NOT EXISTS`만으로는 여전히 "미배치"로
        보인다. 그대로 두면 이 스케줄러가 이미 0으로 빠진
        `USER:*:PENDING_PAYOUT`을 매일 밤 다시 차감하려다
        `InsufficientAvailableError`로 배치 전체가 롤백된다. `ledger_hold.
        reference`(=`f"purchase:{purchase_id}"`)와 `settled_entry_id`(=이
        캡처의 entry_id)를 이용해 `purchase_service.py`가 쓰는 event_ref
        규약(`f"{reference}:release"`)으로 이미 정산됐는지 직접 확인해
        제외한다. R2 ADR이 즉시정산을 없애면 이 특례도 함께 제거될 것."""
        rows = await conn.fetch(
            "SELECT je.entry_id, la.account_code, pl.amount, la.currency, je.posted_at "
            "FROM ledger_journal_entry je "
            "JOIN ledger_posting_line pl ON pl.entry_id = je.entry_id AND pl.side = 'CREDIT' "
            "JOIN ledger_account la ON la.account_id = pl.account_id "
            "WHERE je.event_type = 'HOLD_CAPTURED' "
            "AND la.account_code LIKE 'USER:%:PENDING_PAYOUT' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM ledger_payout_item lpi WHERE lpi.capture_entry_id = je.entry_id"
            ") "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM ledger_hold lh "
            "  JOIN ledger_journal_entry release_je "
            "    ON release_je.event_ref = lh.reference || ':release' "
            "    AND release_je.event_type = 'PAYOUT_RELEASE' "
            "  WHERE lh.settled_entry_id = je.entry_id"
            ") "
            "ORDER BY je.posted_at"
        )
        records = []
        for row in rows:
            seller_user_id = parse_account_code(row["account_code"]).user_id
            assert seller_user_id is not None  # WHERE절이 USER:*:PENDING_PAYOUT만 골랐다
            records.append(
                CaptureRecord(
                    entry_id=row["entry_id"],
                    seller_user_id=seller_user_id,
                    amount=row["amount"],
                    currency=Currency(row["currency"]),
                    captured_at=row["posted_at"],
                )
            )
        return records

    async def run_payout_once(self, now: datetime) -> list[PayoutBatchView]:
        """정산 한 주기: 미배치 캡처를 조회해 `schedule_payouts`(LC-15a)에
        위임한다. 후보가 없으면 트랜잭션도 열지 않는다."""
        period_end: date = now.astimezone(_KST).date()
        period_start = period_end - _PAYOUT_CAPTURE_LOOKBACK
        async with self._pool.acquire() as conn, conn.transaction():
            captures = await self._fetch_payout_capture_candidates(conn)
            if not captures:
                return []
            return await schedule_payouts(
                conn,
                captures,
                period_start=period_start,
                period_end=period_end,
                now=now,
                actor_subject_id=None,
                journal=self._journal,
                balances=self._balances,
                audit=self._audit,
                clock=self._clock,
                payouts=self._payouts,
                settlement_window=self._settlement_window,
            )

    async def run_payout_forever(self) -> None:
        """main.py 백그라운드 태스크 본체(정산 루프, LC-16). 매일 00:10 KST에
        한 번만 깨어난다 — 한 주기의 실패가 루프를 죽이지 않는다(위 무결성
        루프와 동일 설계)."""
        while True:
            await asyncio.sleep(_seconds_until_next_run(self._clock(), self._payout_run_time))
            try:
                await self.run_payout_once(self._clock())
            except Exception:
                logger.exception("ledger_payout: 이번 주기 정산 스케줄 실패 — 다음 주기에 재시도")
