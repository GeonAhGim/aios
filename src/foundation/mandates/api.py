"""mandates aggregate's cross-aggregate import surface.

Other foundation aggregates that need mandates' domain enums by identity
(not just structural/string compatibility with `contracts.v1`'s versions)
import them from here instead of reaching into `mandates.domain.*` directly
(`boundary:foundation-aggregates`, task-5418). Pure re-export, no behavior
change: this is the same class object as `domain.models.
MandateRevisionState`, not a converted copy.
"""
from __future__ import annotations

from src.foundation.mandates.domain.models import MandateRevisionState

__all__ = ["MandateRevisionState"]
