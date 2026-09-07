"""`tests/adversarial/auth/` shared fixtures.

Re-exports the `pool` fixture from `tests/integration/auth/conftest.py`
(same pattern as `tests/adversarial/oms/conftest.py`) — no new connection
pool logic here.
"""
from __future__ import annotations

from tests.integration.auth.conftest import pool

__all__ = ["pool"]
