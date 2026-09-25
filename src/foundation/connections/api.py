"""connections aggregate's cross-aggregate import surface.

Other foundation aggregates that need connections' domain enums by identity
(not just structural/string compatibility with `contracts.v1`'s versions)
import them from here instead of reaching into `connections.domain.*`
directly (`boundary:foundation-aggregates`, task-5418). Pure re-export, no
behavior change: this is the same class object as
`domain.models.ConnectionState`/`HealthState`, not a converted copy.
"""
from __future__ import annotations

from src.foundation.connections.domain.models import ConnectionState, HealthState

__all__ = ["ConnectionState", "HealthState"]
