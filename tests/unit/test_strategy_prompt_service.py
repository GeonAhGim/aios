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


async def test_generate_empty_string_raises_unavailable():
    """negative: 빈 문자열 입력 시에도 unavailable 예외가 발생한다."""
    service = StrategyPromptService()

    with pytest.raises(PromptGenerationUnavailableError):
        await service.generate("")


async def test_generate_none_input_raises_unavailable():
    """negative: None 입력 시 unavailable 예외가 발생한다."""
    service = StrategyPromptService()

    with pytest.raises(PromptGenerationUnavailableError):
        await service.generate(None)  # pyright: ignore[reportArgumentType]


async def test_build_system_prompt_raises_unavailable():
    """negative: _build_system_prompt 내부에서도 unavailable 예외가 발생한다 (line-coverage)."""
    service = StrategyPromptService()

    with pytest.raises(PromptGenerationUnavailableError):
        await service.generate("테스트")
    # _build_system_prompt가 generate 내부에서 호출되므로,
    # raise가 발생하는 경로를 함께 커버한다.


async def test_error_message_contains_unavailable_keyword():
    """negative: 예외 메시지에 '비활성화' 키워드가 포함되어 고객에게 정확한 상태를 전달한다."""
    service = StrategyPromptService()

    with pytest.raises(PromptGenerationUnavailableError, match="비활성화"):
        await service.generate("매수 신호만 알려줘")


async def test_error_is_not_generic_exception():
    """negative: PromptGenerationUnavailableError는 Generic Exception과 구분된다 —
    다른 예외를 catch하는 블록에서 누수되지 않는다."""
    service = StrategyPromptService()

    with pytest.raises(PromptGenerationUnavailableError):
        try:
            await service.generate("테스트")
        except ValueError:
            pytest.fail("ValueError should not catch PromptGenerationUnavailableError")


async def test_multiple_calls_all_raise_unavailable():
    """negative: 여러 호출에서 매번 동일한 예외가 발생한다 (statelessness 확인)."""
    service = StrategyPromptService()

    for _ in range(3):
        with pytest.raises(PromptGenerationUnavailableError):
            await service.generate("different prompt each time")
