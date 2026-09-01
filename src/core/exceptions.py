"""2.12b — 커스텀 예외 계층.

Spec: 11_implementation_rules_v1.2.md#§11.3
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.data.models.base import Currency


class MihwaError(Exception):
    """모든 프로젝트 커스텀 예외의 루트."""


class CurrencyMismatchError(MihwaError):
    def __init__(self, c1: Currency, c2: Currency):
        super().__init__(f"통화 불일치: {c1} vs {c2}")


class ExchangeAPIError(MihwaError):
    """거래소 API 호출 실패 공통 부모. 재시도 가능 여부를 서브클래스로 구분."""


class RetryableExchangeError(ExchangeAPIError):
    ...


class FatalExchangeError(ExchangeAPIError):
    """인증 실패 등 재시도 무의미."""


class ZoneViolationError(MihwaError):
    """15.6-A FROZEN Zone 경로를 SCAFFOLD 코드가 잘못 import하려 할 때(런타임 방어선)."""


class FrozenZoneLiveModeBlockedError(MihwaError):
    """ADR-2026-08-29-E — FROZEN-PAPER-ONLY 하드 가드. Executor.execute()가
    mode != 'PAPER'인 실행을 넘겨받으면 정책 문서상 금지가 아니라 이
    예외로 실제 코드 레벨에서 차단한다. 15.6-D 조건 2(실계정 MFA·이중승인
    운영 적용) 충족 후 별도 ADR로만 제거 가능 — 이 세션의 어떤 leaf도
    이 가드를 우회하거나 약화시켜서는 안 된다."""


class FrozenZonePaperAdapterBlockedError(MihwaError):
    """PAPER execution에 live-configured adapter가 주입된 경우의 fail-closed guard."""


class EventHandlerError(MihwaError):
    ...
