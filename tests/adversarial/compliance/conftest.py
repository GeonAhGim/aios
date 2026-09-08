"""`tests/adversarial/compliance/` shared fixtures.

Re-exports `pool`/`repo`/`trust_repo`/`audit_repo` from
`tests/foundation/integration/mandates/conftest.py` (same pattern as
`tests/adversarial/auth/conftest.py`) — no new connection pool logic here.
"""
from __future__ import annotations

from tests.foundation.integration.mandates.conftest import (
    audit_repo,
    default_rules,
    pool,
    repo,
    trust_repo,
)

__all__ = ["audit_repo", "default_rules", "pool", "repo", "trust_repo"]
