"""10.3 — Emergency withdrawal panic prompt generator.

Spec: design_doc_v1.20.md#FD-10.3, policy document 7.10-A

When a severe counterparty (exchange) risk signal is detected, this module
generates a prompt using only pre-registered withdrawal destination whitelists.
Because there is no path for entering a new destination address, social-engineering
attacks that impersonate a crisis to lure users into an attacker-controlled address
are blocked at the source (policy document 20.1-B "prepare before crisis" principle).

The fast path is opened only when at least 2 independent sources corroborate without
contradiction. If there is only 1 source or they contradict each other, the prompt
falls back to the FD-10.1 general approval procedure rather than opening a "fast path"
in an uncertain situation.

Whitelist lookup is injected via a DI callback (a pattern repeated in this session).
(Note — WithdrawalWhitelistService.fetch_for_panic_prompt() now actually satisfies
this signature, so wiring is possible. However, the real trigger for this generator,
"severe counterparty risk signal detection," requires an automated monitoring pipeline
(the same reason as Watchdog, FD-9), so there is no HTTP endpoint to expose. Building
an API without knowing who fills in the corroboration signals would be speculative
implementation.)
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.approval import service as approval

MIN_CORROBORATION_SOURCES = 2


class WhitelistEntry(BaseModel):
    id: int
    exchange: str
    destination_address: str
    label: str | None = None


class CorroborationSignal(BaseModel):
    source: str
    risk_confirmed: bool


class PanicPromptResult(BaseModel):
    fast_path_activated: bool
    destinations: list[WhitelistEntry]
    fallback_approval_request_id: int | None = None


FetchWhitelistFn = Callable[[UUID, str], Awaitable[list[WhitelistEntry]]]


def _corroborates(signals: list[CorroborationSignal]) -> bool:
    """At least 2 sources must all confirm risk to count as corroboration — if
    there is only 1 source or any one denies it (contradiction), do not trust."""
    if len(signals) < MIN_CORROBORATION_SOURCES:
        return False
    return all(signal.risk_confirmed for signal in signals)


class PanicPromptGenerator:
    def __init__(self, pool: asyncpg.Pool, *, fetch_whitelist: FetchWhitelistFn) -> None:
        self._pool = pool
        self._fetch_whitelist = fetch_whitelist

    async def generate(
        self,
        *,
        user_id: UUID,
        exchange: str,
        corroboration: list[CorroborationSignal],
    ) -> PanicPromptResult:
        if not _corroborates(corroboration):
            request = await approval.create_request(
                self._pool,
                scope="USER",
                user_id=user_id,
                trigger_source="counterparty_risk_panic_uncorroborated",
                requested_action="EMERGENCY_WITHDRAWAL_REVIEW",
                context={
                    "exchange": exchange,
                    "corroboration_sources": [s.source for s in corroboration],
                },
                approval_mode="SOLO",
            )
            return PanicPromptResult(
                fast_path_activated=False,
                destinations=[],
                fallback_approval_request_id=request.id,
            )

        destinations = await self._fetch_whitelist(user_id, exchange)
        return PanicPromptResult(fast_path_activated=True, destinations=destinations)
