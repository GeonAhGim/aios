"""FD-14.2 단위테스트 — 자연어 프롬프트 전략 생성(현재 비활성화 상태)."""

import time

import pytest

from src.services import strategy_prompt_service as strategy_prompt_service_module
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


async def test_generate_propagates_unexpected_exception_from_dependency(monkeypatch):
    """실패주입: 예외 생성 경로 자체가 깨져도(RuntimeError) fail-closed로
    전파되어야 한다 — PromptGenerationUnavailableError로 위장해 삼키면 안 된다."""

    def _broken_init(self, *args, **kwargs):
        raise RuntimeError("injected dependency failure")

    monkeypatch.setattr(
        strategy_prompt_service_module.PromptGenerationUnavailableError,
        "__init__",
        _broken_init,
    )
    service = StrategyPromptService()

    with pytest.raises(RuntimeError, match="injected dependency failure"):
        await service.generate("실패주입 테스트")


@pytest.mark.perf
async def test_generate_p95_latency_within_budget():
    """성능단언: generate()는 의존성 호출 없이 즉시 예외를 발생시키므로
    p95 지연은 10ms 예산 이내여야 한다 (ADR-2026-09-09-C 결정1 준용)."""
    service = StrategyPromptService()
    samples = []

    for _ in range(50):
        start = time.perf_counter()
        with pytest.raises(PromptGenerationUnavailableError):
            await service.generate("성능 측정용 프롬프트")
        samples.append(time.perf_counter() - start)

    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1]
    assert p95 < 0.01, f"p95 latency {p95:.4f}s exceeded 10ms budget"
