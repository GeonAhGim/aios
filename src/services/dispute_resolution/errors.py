"""18.2 — shared exception for the dispute resolution package."""
from __future__ import annotations


class DisputeResolutionError(Exception):
    """FD-18.2 failure — router converts to 400/404."""
