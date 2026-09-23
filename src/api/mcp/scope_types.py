"""Shared type alias for MCP scope-dependency callables.

Split out of `server.py` so `tools_*.py` can type-hint `require_scope`
parameters without importing `server` (which itself imports every
`tools_*` module to assemble the app) -- that round trip is a real import
cycle, not just a lint artifact (`check_import_linter.py` walks all
`ImportFrom` targets including `TYPE_CHECKING`-guarded ones).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.foundation.ai.gateway.domain.token_rules import AgentToken

ScopeDependency = Callable[..., Awaitable[AgentToken]]

__all__ = ["ScopeDependency"]
