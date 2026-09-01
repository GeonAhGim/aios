"""FD-14.2 단위테스트 — 자연어 프롬프트 전략 생성(현재 비활성화 상태)."""

import pytest

from src.services.strategy_prompt_service import (
    PromptGenerationUnavailableError,
    StrategyPromptService,
)


async def test_generate_raises_unavailable_until_ai_backend_configured():
    service = StrategyPromptService()

    with pytest.raises(PromptGenerationUnavailableError):
        await service.generate("RSI 과매도에서 반등 매수하는 전략 만들어줘")
