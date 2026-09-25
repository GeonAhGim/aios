"""U-3a -- application/explain_script.py 단위 테스트."""

from __future__ import annotations

from src.core.indicators.registry import DEFAULT_REGISTRY
from src.foundation.ai.assistant.application.explain_script import (
    ExplainScriptCompileFailure,
    ExplainScriptSuccess,
    explain_script,
)
from src.foundation.ai.assistant.ports.script_provider import ScriptDraft

REGISTRY_VERSION = DEFAULT_REGISTRY.registry_hash()
VALID_SCRIPT = (
    "input length: int = 14\n"
    "input close: series<float> = 0\n"
    "let rsi_val = ta.rsi(close, length)\n"
    "signal go_long = rsi_val < 30\n"
    "plot(rsi_val, 1)\n"
)


class FakeProvider:
    async def generate_script(self, *, prompt: str, hint: str | None = None) -> ScriptDraft:
        raise NotImplementedError

    async def explain_script(self, *, source: str) -> str:
        return "RSI가 30 밑으로 내려가면 매수 신호를 냅니다."

    async def explain_backtest(self, *, summary: str) -> str:
        raise NotImplementedError


async def test_valid_script_is_explained() -> None:
    result = await explain_script(
        source=VALID_SCRIPT, provider=FakeProvider(), registry_version=REGISTRY_VERSION
    )
    assert isinstance(result, ExplainScriptSuccess)
    assert result.explanation
    assert result.script_hash


async def test_broken_script_is_not_explained_but_reports_error() -> None:
    result = await explain_script(
        source="let a = 1 +", provider=FakeProvider(), registry_version=REGISTRY_VERSION
    )
    assert isinstance(result, ExplainScriptCompileFailure)
    assert result.error_code == "SCRIPT_SYNTAX"
    assert result.suggestion
