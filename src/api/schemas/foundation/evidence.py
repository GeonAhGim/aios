"""Audit Evidence API response schema — HTTP details only here; the contract itself
wraps `src/foundation/evidence/contracts/v1.py` (106 §2).

Write endpoints are intentionally absent — audit events are recorded internally
as side effects of commands in other bounded contexts; if users could create them
directly via HTTP, audit log integrity would lose its meaning (79 §1, the
"append-only" principle includes "no one can create arbitrarily")."""
from __future__ import annotations

from src.foundation.evidence.contracts.v1 import AuditEventView, AuditTimelinePage

__all__ = ["AuditEventView", "AuditTimelinePage"]
