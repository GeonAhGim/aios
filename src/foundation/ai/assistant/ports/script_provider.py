"""U-3a -- narrow model-provider port owned by the assistant leaf.

AI-5 (`src/foundation/ai/providers/ports/model_provider.py`, task-2639) will
eventually expose a general `ModelProvider.generate(schema, prompt, budget)
-> StructuredOutput` port, but it does not exist at commit time (inflight).
This file exposes only the minimal surface U-3a's three use cases need
(script generation/explanation, backtest narration) -- not that general
port. Once AI-5 lands, the adapter (`adapters/anthropic_provider.py`) is
expected to be rewired thinly on top of it; this port is not removed now
(there is nothing to replace it with yet).

Structured output only (same principle as AI-5): `generate_script` returns
only `source` (the raw AIOS Script text), not free-form prose. The rest
(explanation/narration) returns plain text but carries no executable side
effect (the Protocol's return type gives it no means to have one).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ScriptDraft:
    """An AIOS Script draft produced by a provider. Whether it compiles is
    checked separately by the caller (`application/generate_script.py`) via
    `compile_source` -- this value is not itself a compilation guarantee."""

    source: str
    provider_name: str
    model_name: str


class ScriptGenerationProvider(Protocol):
    """Defines only the three use cases U-3a needs (a separate, narrow
    contract, not a subset of AI-5's general port -- see the module
    docstring above)."""

    async def generate_script(self, *, prompt: str, hint: str | None = None) -> ScriptDraft:
        """Generate an AIOS Script draft from the natural-language `prompt`.
        `hint` is extra context for a retry (e.g. the previous compile
        failure reason), or None."""
        ...

    async def explain_script(self, *, source: str) -> str:
        """Turn an AIOS Script source that already compiled successfully
        into a human-readable natural-language explanation."""
        ...

    async def explain_backtest(self, *, summary: str) -> str:
        """Turn an already-computed backtest metrics summary (a string --
        the caller serializes any Decimal to a fixed-point string) into a
        natural-language narrative. Does not re-derive the original
        fill/account data (same principle as UX-A5 -- narrate only already-
        computed values)."""
        ...
