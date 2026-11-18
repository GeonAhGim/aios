"""Shared application-layer errors for the mandates context.

`NoActiveMandateError` must be a single class: the exception->HTTP registry
matches by class identity, so per-module copies silently fall through to 500.
"""

from __future__ import annotations


class NoActiveMandateError(Exception):
    """The tenant has no mandate or no active revision to act on."""
