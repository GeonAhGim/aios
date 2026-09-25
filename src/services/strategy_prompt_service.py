"""FD-14.2 (new) — Natural-language prompt-based strategy generation (AI, currently inactive).

Spec: ADR-2026-08-29-wallet-marketplace-dual-seller-strategy-authoring.md §3
— The remaining axis decided to run alongside the goal-based wizard
(strategy_wizard_service.py). The ultimate goal is to pass free-text
input to Claude (Anthropic) and receive the same condition schema as
strategy_wizard_service.GeneratedConditions, but at this leaf point
the Anthropic API credit balance is $0
(see DevEngine/AIOS shared project memory), so real calls always fail.

Rather than fabricating a "plausible fake response" to mask failure as
success, we return the unimplemented state honestly (principle from
document #11 — do not disguise unverifiable states as success, the same
pattern applied repeatedly in documents #12 and #13). Once credits are
loaded, only the Anthropic client call inside this service needs to be
replaced — the router and schema are already in their final form
(return type of generate() is GeneratedConditions as-is).
"""
from __future__ import annotations

from src.services.strategy_wizard_service import GeneratedConditions


class PromptGenerationUnavailableError(Exception):
    """Anthropic API credit not loaded — router converts to 501."""


class StrategyPromptService:
    async def generate(self, prompt: str) -> GeneratedConditions:
        raise PromptGenerationUnavailableError(
            "자연어 기반 전략 생성 기능은 아직 비활성화 상태입니다"
            "(Anthropic API 크레딧 충전 대기 중). 목표기반 마법사를 사용해주세요."
        )
