"""2.12b — Custom exception hierarchy.

Spec: 11_implementation_rules_v1.2.md#§11.3
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.data.models.base import Currency


class MihwaError(Exception):
    """Root of all project custom exceptions."""


class CurrencyMismatchError(MihwaError):
    def __init__(self, c1: Currency, c2: Currency):
        super().__init__(f"통화 불일치: {c1} vs {c2}")


class ExchangeAPIError(MihwaError):
    """Common parent for exchange API call failures. Subclasses distinguish retryability."""


class RetryableExchangeError(ExchangeAPIError):
    ...


class FatalExchangeError(ExchangeAPIError):
    """Retry futile (e.g., authentication failure)."""


class ZoneViolationError(MihwaError):
    """Runtime guard: SCAFFOLD code incorrectly imports a 15.6-A FROZEN Zone path."""


class FrozenZoneLiveModeBlockedError(MihwaError):
    """ADR-2026-08-29-E — FROZEN-PAPER-ONLY hard guard. When Executor.execute()
    receives a call with mode != 'PAPER', this exception blocks it at the
    code level rather than relying on policy documents alone. Can only be
    removed via a separate ADR after satisfying 15.6-D Condition 2 (live
    account MFA/dual-approval operation). No leaf in this session may
    bypass or weaken this guard."""


class FrozenZonePaperAdapterBlockedError(MihwaError):
    """Fail-closed guard: live-configured adapter injected into PAPER execution."""


class EventHandlerError(MihwaError):
    ...
