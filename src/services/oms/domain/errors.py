"""OMS 에러 taxonomy(L4 명세 §3.4).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §3.4, §9 L4-01.
"""
from __future__ import annotations

from src.core.exceptions import MihwaError


class OmsError(MihwaError):
    """OMS 도메인 에러의 공통 베이스. `code`는 §3.4의 `OMS_*` 상수 중 하나."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class InvalidOrderTransitionError(OmsError):
    """OMS_INVALID_TRANSITION — 전이표 밖의 (from, event) 조합. 재시도 불가,
    코드 결함 신호(§3.4)."""

    def __init__(self, message: str) -> None:
        super().__init__("OMS_INVALID_TRANSITION", message)


class OrderValidationError(OmsError):
    """OMS_VALIDATION_* — tick/lot/min-notional/미지원 타입·TIF/미등록 심볼/
    장 마감 중 하나. `reason` 값 자체가 §3.4의 접미어(예: "MIN_NOTIONAL")다."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(f"OMS_VALIDATION_{reason}", message or reason)
        self.reason = reason


class UnknownSymbolError(OrderValidationError):
    def __init__(self, symbol: str, venue: str) -> None:
        super().__init__("UNKNOWN_SYMBOL", f"미등록 심볼입니다: {symbol}@{venue}")
        self.symbol = symbol
        self.venue = venue


class UnsupportedVenueFeatureError(OmsError):
    """OMS_ALGO_NOT_ENABLED 등 — venue/phase가 지원하지 않는 기능 요청."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message)


class IdempotencyDigestMismatchError(OmsError):
    """OMS_IDEMPOTENCY_DIGEST_MISMATCH — 같은 scope_hash인데 command_digest가
    다르다. 재시도 불가, 상위 버그 신호(§3.4)."""

    def __init__(self, scope_hash: str) -> None:
        super().__init__(
            "OMS_IDEMPOTENCY_DIGEST_MISMATCH",
            f"scope_hash={scope_hash}: 같은 멱등 스코프에 다른 내용의 명령이 재사용됐습니다.",
        )
