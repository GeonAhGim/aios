"""AI-15 -- Agent Gateway MCP server assembly.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1 (`src/api/mcp/
server.py`: "MCP server (stdio+HTTP). Each tool calls exactly one REST use
case (zero logic)"), §9 AI-15 DoD (I-08: "the MCP/tool server holds no more
authorization/business logic than the REST layer already enforces").

This module ships the HTTP transport only -- the stdio transport named in
the spec requires the `mcp` SDK, whose license/dependency has not yet been
confirmed by AI-3 (`docs/design/AI_DEPENDENCIES_EVAL.md` does not exist in
this tree as of this leaf; CLAUDE.md §4 "swapping a third-party dependency
without an explicit architecture decision" forbids bringing it in here).
The HTTP transport needs no such SDK: it is a plain FastAPI app, the same
ASGI stack every other `src/api/*` router already uses.

`require_scope()` is this leaf's entire authorization surface, and it is
deliberately thin: it does exactly what §3 states plainly --

- "Authorization: Bearer <human JWT>" and "X-AIOS-Agent-Token" are two
  separate credential channels (independent middleware, never merged) -- a
  request carrying `Authorization` is rejected outright, whether or not it
  also carries a valid agent token, and *before* any DB round trip. A human
  session (PLT-24's `get_current_user`/`AuthenticatedUser`) never grants MCP
  tool access -- inheriting human session authority into an agent context is
  exactly what I-06/AI-2 exist to prevent.
- A present `X-AIOS-Agent-Token` is checked by AI-4's `authorize()`, the
  single authorization point for every MCP tool (§2.1) -- this module does
  not re-derive or duplicate any of AI-2's scope/liveness judgment, it only
  maps `TokenRuleError`'s two sub-families onto the two HTTP status codes
  §3's error catalog names for them.

`create_mcp_app()` takes an already-open `asyncpg.Pool` rather than owning
its own lifespan -- the pool this server's tools query (agent_token,
experiments, indicator inputs) is the same pool `src/main.py`'s lifespan
already creates for the human-facing REST app; this leaf does not duplicate
that wiring, it is the caller's job to hand this factory a pool (production
wiring, deferred: no leaf before AI-17 mounts this app anywhere).
"""

from __future__ import annotations

import asyncpg
from fastapi import FastAPI, Header, HTTPException, Request, status

from src.api.mcp import (
    tools_paper,
    tools_propose,
    tools_read,
    tools_research,
    tools_research_data,
)
from src.api.mcp.scope_types import ScopeDependency
from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.application.authorize import authorize
from src.foundation.ai.gateway.domain.token_rules import (
    AgentToken,
    Scope,
    TokenExpiredError,
    TokenRevokedError,
    TokenRuleError,
)

__all__ = ["AGENT_TOKEN_HEADER", "ScopeDependency", "create_mcp_app", "require_scope"]

AGENT_TOKEN_HEADER = "X-AIOS-Agent-Token"


def require_scope(scope: Scope) -> ScopeDependency:
    """Build a FastAPI dependency that authorizes `scope` via AI-4's
    `authorize()` -- one closure per scope, reused across every tool
    endpoint that needs it (`tools_read.py` uses `Scope.READ`,
    `tools_research.py` uses `Scope.RESEARCH`)."""

    async def _dependency(
        request: Request,
        authorization: str | None = Header(default=None),
        x_aios_agent_token: str | None = Header(default=None, alias=AGENT_TOKEN_HEADER),
    ) -> AgentToken:
        if authorization is not None:
            # §3: a human JWT is a different credential channel entirely --
            # rejected on sight, never even considered as a fallback, and
            # never mixed with agent-token verification below.
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "AI_TOKEN_REVOKED: human session credentials (Authorization) "
                "are not accepted by the agent gateway MCP server",
            )
        if x_aios_agent_token is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                f"AI_TOKEN_REVOKED: missing {AGENT_TOKEN_HEADER} header",
            )

        pool: asyncpg.Pool = request.app.state.pool
        repo = PostgresAgentTokenRepository(pool)
        try:
            return await authorize(
                repo,
                token_secret=x_aios_agent_token,
                scope=scope,
            )
        except (TokenRevokedError, TokenExpiredError) as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"AI_TOKEN_REVOKED: {exc}") from exc
        except TokenRuleError as exc:
            # ScopeDeniedError / InstrumentNotAllowedError / NotionalCapExceededError
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"AI_SCOPE_DENIED: {exc}") from exc

    return _dependency


def create_mcp_app(pool: asyncpg.Pool) -> FastAPI:
    """Assemble the full read+research+research_data+propose+paper MCP HTTP
    app over `pool` -- AI-16 mounts the last two routers (`tools_propose`/
    `tools_paper`), RD-16 mounts `tools_research_data`."""
    app = FastAPI(title="AIOS Agent Gateway MCP Server")
    app.state.pool = pool
    app.include_router(tools_read.build_router(require_scope))
    app.include_router(tools_research.build_router(require_scope))
    app.include_router(tools_research_data.build_router(require_scope))
    app.include_router(tools_propose.build_router(require_scope))
    app.include_router(tools_paper.build_router(require_scope))
    return app
