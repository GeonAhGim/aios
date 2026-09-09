"""`tests/foundation/integration/compliance/` shared fixtures.

Re-exports `pool`/`repo`/`trust_repo` from `tests/foundation/integration/
mandates/conftest.py` (same pattern as `tests/adversarial/compliance/
conftest.py`) — no new connection pool logic here.
"""
from __future__ import annotations

from tests.foundation.integration.mandates.conftest import pool, repo, trust_repo

__all__ = ["pool", "repo", "trust_repo"]
