"""U-3a -- test_explain_backtest split: metrics-only narration (task-16695)."""

from __future__ import annotations

from src.foundation.ai.assistant.application.explain_backtest import explain_backtest
from src.foundation.ai.assistant.ports.script_provider import (
    ScriptDraft,
)


class _RecordingProvider:
    """Minimal provider stub for metrics-only tests.

    Shared across sibling test files; imported read-only.
    """

    def __init__(self) -> None:
        self.summaries: list[str] = []

    async def generate_script(self, *, prompt: str, hint: str | None = None) -> ScriptDraft:
        return ScriptDraft(source="", provider_name="recording", model_name="stub")

    async def explain_script(self, *, source: str) -> str:
        return "[stub] script explained"

    async def explain_backtest(self, *, summary: str) -> str:
        self.summaries.append(summary)
        return "최종 자산이 시작 대비 증가했습니다."


async def test_explains_given_metrics_only() -> None:
    provider = _RecordingProvider()
    result = await explain_backtest(
        metrics={"final_equity": "1050.00", "bars": "120"}, provider=provider
    )
    assert result.narrative
    assert "1050.00" in provider.summaries[0]
    assert result.injection.detected is False
