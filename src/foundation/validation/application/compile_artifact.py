"""L4_strategy_portfolio_backtest_v1.0.md §9 L41 -- application/
compile_artifact.py: bridges the strategy_builder bounded context (mutable
FSM definitions, lifecycle-gated) to validation's `domain/artifact.py`
content-addressed `StrategyArtifact` (L36).

`domain/artifact.py`'s L36 docstring notes `compiler_version` is accepted as
an opaque caller-supplied string because there is no separate textual
compiler step for FSM strategy definitions (unlike DSL-11's AIOS Script
path, `src/core/strategy/script_facade.py`) -- an `FSMStrategyConfig` is
already a structured, parsed tree once it leaves `strategy_builder_service`.
`COMPILER_VERSION` below names *this* leaf's compilation rule (verbatim
`fsm_definition` passthrough, no transform) so a real future compiler can
supply its own version string without `domain/artifact.py` changing.

This function performs no I/O of its own beyond the read already exposed by
`StrategyBuilderService.get_strategy` -- it does not persist the resulting
`StrategyArtifact` (no artifact repository/migration exists yet; per
`domain/models.py`'s L37 docstring, that wiring is a separate leaf).
"""

from __future__ import annotations

from uuid import UUID

from src.foundation.validation.domain.artifact import StrategyArtifact, build_artifact
from src.services.strategy_builder_service import StrategyBuilderService, StrategyLifecycleError

COMPILER_VERSION = "fsm-v1"

__all__ = ["COMPILER_VERSION", "StrategyNotFoundForCompilationError", "compile_artifact"]


class StrategyNotFoundForCompilationError(Exception):
    """The strategy does not exist (`StrategyNotFoundError`) or the caller is
    not its owner -- `StrategyBuilderService.get_strategy` hides both cases
    behind the same message (FD-14)."""


async def compile_artifact(
    strategy_service: StrategyBuilderService,
    *,
    owner_user_id: UUID,
    strategy_id: str,
    strategy_version: str,
) -> StrategyArtifact:
    """Read `strategy_service`'s current `fsm_definition` and build the
    content-addressed `StrategyArtifact` for that shape. Does not gate on
    lifecycle status -- any stage must be able to answer "this shape hashes
    to this value"; "when validation may start" is a gate `start_validation`/
    `run_check` decide, not this function."""
    try:
        detail = await strategy_service.get_strategy(owner_user_id, strategy_id, strategy_version)
    except StrategyLifecycleError as exc:
        raise StrategyNotFoundForCompilationError(str(exc)) from exc

    return build_artifact(
        strategy_id=strategy_id,
        version=strategy_version,
        fsm_definition=detail.fsm_definition,
        compiler_version=COMPILER_VERSION,
    )
