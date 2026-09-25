"""DC-26 boundary: Greek calculation is delegated to IND.

This module intentionally contains no Black-Scholes or exchange-specific
formula.  Option-chain transport may create an indicator request, while the
IND subsystem remains authoritative for formulas, inputs, versioning, and
provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class GreekCalculationUnavailableError(RuntimeError):
    """Raised when a caller asks this boundary to calculate Greeks locally."""


@dataclass(frozen=True)
class GreekIndicatorRequest:
    tenant_id: UUID
    instrument_id: UUID
    indicator_name: str = "option_greeks"


class IndicatorGreekDelegate(Protocol):
    async def request_greeks(self, request: GreekIndicatorRequest) -> str:
        """Return an IND run/reference id for the requested Greek series."""
        ...


class GreekCalculator:
    """Small adapter that makes the IND ownership boundary explicit."""

    def __init__(self, delegate: IndicatorGreekDelegate | None = None) -> None:
        self._delegate = delegate

    async def calculate(self, request: GreekIndicatorRequest) -> str:
        if self._delegate is None:
            raise GreekCalculationUnavailableError(
                "Greek calculation is owned by IND; no indicator delegate is configured"
            )
        return await self._delegate.request_greeks(request)


__all__ = [
    "GreekCalculationUnavailableError",
    "GreekCalculator",
    "GreekIndicatorRequest",
    "IndicatorGreekDelegate",
]
