"""9.3 — Split-Brain 실패도메인 진단.

Spec: 기능설계문서_v1.20.md#FD-9.3, 정책문서 8.6-A-1-2

State DB 연결 단절과 메인 프로세스 장애를 구분한다 — DB만 끊긴 경우
거래소 API 응답을 잠정 진실 소스로 취급해 강제청산 대상에서 제외하고
신규주문만 보류한다.

히스테리시스(9차 레드팀 반영): 장애판정 진입 3초 연속, 정상복귀 판정
10초 연속(비대칭 — 복귀는 더 신중하게). 실제 폴링 주기(Draft 5초)보다
짧은 값이라 "호출 횟수"가 아니라 "실제 경과 시간"으로 판정한다(주기가
불규칙해도 일관되게 동작).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from enum import Enum

from pydantic import BaseModel

logger = logging.getLogger(__name__)

CheckFn = Callable[[], Awaitable[bool]]

DEFAULT_ENTRY_CONFIRM_SECONDS = 3.0
DEFAULT_RECOVERY_CONFIRM_SECONDS = 10.0
DEFAULT_CHECK_TIMEOUT_SECONDS = 5.0


class Diagnosis(str, Enum):
    NORMAL = "NORMAL"
    DB_ISOLATED_FAILURE = "DB_ISOLATED_FAILURE"  # DB만 단독 장애 — 강제청산 제외 대상
    APPLY_WATCHDOG_DECISION = "APPLY_WATCHDOG_DECISION"  # FD-9.2 기존 판정 그대로 적용


class FailureDomain(BaseModel):
    db_ok: bool
    exchange_ok: bool
    main_process_ok: bool
    diagnosis: Diagnosis


class _StreakTracker:
    """진입/복귀에 서로 다른 지속시간을 요구하는 비대칭 히스테리시스."""

    def __init__(self, entry_seconds: float, recovery_seconds: float) -> None:
        self._entry_seconds = entry_seconds
        self._recovery_seconds = recovery_seconds
        self._confirmed_ok = True
        self._pending_since: float | None = None
        self._pending_value: bool | None = None

    def update(self, raw_ok: bool) -> bool:
        now = time.monotonic()
        if raw_ok == self._confirmed_ok:
            self._pending_since = None
            self._pending_value = None
            return self._confirmed_ok

        threshold = self._recovery_seconds if raw_ok else self._entry_seconds
        if self._pending_value != raw_ok:
            self._pending_value = raw_ok
            self._pending_since = now
        if self._pending_since is not None and now - self._pending_since >= threshold:
            self._confirmed_ok = raw_ok
            self._pending_since = None
            self._pending_value = None
        return self._confirmed_ok


async def _safe_check(check_fn: CheckFn, *, timeout: float) -> bool:
    """예외상황 — 판정 로직 자체가 타임아웃/예외로 응답 없으면 False로
    간주한다(응답 없음=장애 의심, 낙관적으로 True 취급하지 않는다)."""
    try:
        return bool(await asyncio.wait_for(check_fn(), timeout=timeout))
    except Exception:  # noqa: BLE001
        logger.warning("Split-Brain 체크 실패/타임아웃 — False로 처리", exc_info=True)
        return False


class SplitBrainDiagnostics:
    def __init__(
        self,
        *,
        entry_confirm_seconds: float = DEFAULT_ENTRY_CONFIRM_SECONDS,
        recovery_confirm_seconds: float = DEFAULT_RECOVERY_CONFIRM_SECONDS,
        check_timeout_seconds: float = DEFAULT_CHECK_TIMEOUT_SECONDS,
    ) -> None:
        self._check_timeout_seconds = check_timeout_seconds
        self._exchange_tracker = _StreakTracker(entry_confirm_seconds, recovery_confirm_seconds)
        self._db_tracker = _StreakTracker(entry_confirm_seconds, recovery_confirm_seconds)
        self._main_process_tracker = _StreakTracker(entry_confirm_seconds, recovery_confirm_seconds)

    async def diagnose(
        self,
        *,
        check_exchange: CheckFn,
        check_db: CheckFn,
        main_process_ok_raw: bool,
    ) -> FailureDomain:
        exchange_ok_raw = await _safe_check(check_exchange, timeout=self._check_timeout_seconds)
        db_ok_raw = await _safe_check(check_db, timeout=self._check_timeout_seconds)

        exchange_ok = self._exchange_tracker.update(exchange_ok_raw)
        db_ok = self._db_tracker.update(db_ok_raw)
        main_process_ok = self._main_process_tracker.update(main_process_ok_raw)

        if exchange_ok and main_process_ok and not db_ok:
            diagnosis = Diagnosis.DB_ISOLATED_FAILURE
        elif not exchange_ok or not main_process_ok:
            diagnosis = Diagnosis.APPLY_WATCHDOG_DECISION
        else:
            diagnosis = Diagnosis.NORMAL

        return FailureDomain(
            db_ok=db_ok,
            exchange_ok=exchange_ok,
            main_process_ok=main_process_ok,
            diagnosis=diagnosis,
        )
