"""U-3a -- application/explain_script.py 단위 테스트.

DEEPEN(task-10272): §3.3 타입/룩어헤드/리소스 오류 각각이 설명 없이
`ExplainScriptCompileFailure`로 거부되는지(negative, ≥3건) 보강하고,
provider가 예외를 던지는 실패주입 1건으로 fail-closed(예외가 삼켜지지
않고 그대로 전파)를 확인한다.
"""

from __future__ import annotations

import pytest

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
TYPE_ERROR_SCRIPT = "input a: int = 1\nlet bad = missing\n"
LOOKAHEAD_ERROR_SCRIPT = "input c: series<float> = 0\nlet a = ta.security(c, 1)\n"
RESOURCE_LIMIT_SCRIPT = "input c: series<float> = 0\n" + "plot(c)\n" * 33


class FakeProvider:
    async def generate_script(self, *, prompt: str, hint: str | None = None) -> ScriptDraft:
        return ScriptDraft(source=VALID_SCRIPT, provider_name="fake", model_name="fake-model")

    async def explain_script(self, *, source: str) -> str:
        return "RSI가 30 밑으로 내려가면 매수 신호를 냅니다."

    async def explain_backtest(self, *, summary: str) -> str:
        return "백테스트 결과 요약입니다."


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


async def test_type_error_script_is_not_explained() -> None:
    result = await explain_script(
        source=TYPE_ERROR_SCRIPT, provider=FakeProvider(), registry_version=REGISTRY_VERSION
    )
    assert isinstance(result, ExplainScriptCompileFailure)
    assert result.error_code == "SCRIPT_TYPE"
    assert result.suggestion


async def test_lookahead_error_script_is_not_explained() -> None:
    result = await explain_script(
        source=LOOKAHEAD_ERROR_SCRIPT, provider=FakeProvider(), registry_version=REGISTRY_VERSION
    )
    assert isinstance(result, ExplainScriptCompileFailure)
    assert result.error_code == "SCRIPT_LOOKAHEAD"
    assert result.suggestion


async def test_resource_limit_error_script_is_not_explained() -> None:
    result = await explain_script(
        source=RESOURCE_LIMIT_SCRIPT, provider=FakeProvider(), registry_version=REGISTRY_VERSION
    )
    assert isinstance(result, ExplainScriptCompileFailure)
    assert result.error_code == "SCRIPT_RESOURCE_LIMIT"
    assert result.suggestion


class RaisingProvider(FakeProvider):
    """실패주입: provider.explain_script가 예외를 던지는 의존성 장애를 흉내낸다."""

    async def explain_script(self, *, source: str) -> str:
        raise RuntimeError("provider unavailable")


async def test_provider_failure_propagates_instead_of_being_swallowed() -> None:
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await explain_script(
            source=VALID_SCRIPT, provider=RaisingProvider(), registry_version=REGISTRY_VERSION
        )
