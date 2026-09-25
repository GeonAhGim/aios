"""AI-15 -- Agent Gateway MCP server package (`src/api/mcp/`).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1 (`src/api/mcp/
server.py`, `src/api/mcp/tools_{read,research,propose,paper}.py`), §9 AI-15
row ("thin proxy", DoD I-08). Only `tools_read.py`/`tools_research.py` ship
here -- `tools_propose.py`/`tools_paper.py` are AI-16 (confirmation-token
round trip, not yet implemented).
"""

from __future__ import annotations
