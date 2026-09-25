"""Canonical JSON serialization — RATCHET-2 core-no-io ownership boundary.

`canonical_json` (sorted-key, deterministic JSON) was originally defined in
`src.foundation.ledger.domain.hash_chain` (LC-3) and reused by
`src.core.eventstore.append` (FA-13, task-1703 decision — reuse, don't
reimplement the digest recipe) and `src.core.eventstore.replay` (FA-15).
`.importlinter`'s `core-no-io` forbids `src/core` from importing
`src/foundation`, so the primitive itself — pure, no I/O, no ledger-specific
types — moves here; `hash_chain.py` now imports and re-exports it instead of
defining it, since `src/foundation` is allowed to import `src/core`.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["canonical_json"]


def canonical_json(data: Any) -> str:
    """Deterministic JSON string with sorted keys."""
    return json.dumps(data, sort_keys=True, default=str)
