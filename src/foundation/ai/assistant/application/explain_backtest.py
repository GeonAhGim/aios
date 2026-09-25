"""U-3a -- natural-language narration of an already-computed backtest result.

`src/api/routers/backtests.py` (`POST /v1/backtests/quick`) runs
synchronously and persists nothing, so this use case does not re-fetch by
backtest_id -- the caller (the router) already holds the
`QuickBacktestResultView` fields and serializes them to strings as
`metrics`. This function does not recompute them, only narrates (same
principle as UX-A5: display only stored/computed values, never invent new
figures by inference).

`question` (an optional natural-language question the user appended about
the result) goes through the same injection detection/quarantine as
`generate_script` -- phrasing like "buy into this strategy right now" can
show up on the backtest-narration path too.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src.foundation.ai.assistant.domain import injection_guard
from src.foundation.ai.assistant.domain.injection_guard import InjectionFinding
from src.foundation.ai.assistant.ports.script_provider import ScriptGenerationProvider


@dataclass(frozen=True)
class ExplainBacktestResult:
    narrative: str
    injection: InjectionFinding


def _render_summary(metrics: Mapping[str, str], question: str | None) -> str:
    lines = [f"{key}: {value}" for key, value in sorted(metrics.items())]
    body = "\n".join(lines)
    if question:
        return f"{body}\n\n[user question]\n{question}"
    return body


async def explain_backtest(
    *, metrics: Mapping[str, str], provider: ScriptGenerationProvider, question: str | None = None
) -> ExplainBacktestResult:
    finding = InjectionFinding(detected=False, matched_snippets=())
    quarantined_question = question
    if question:
        finding = injection_guard.detect_injection(question)
        quarantined_question = injection_guard.quarantine_for_provider(question, finding)

    summary = _render_summary(metrics, quarantined_question)
    narrative = await provider.explain_backtest(summary=summary)
    return ExplainBacktestResult(narrative=narrative, injection=finding)
