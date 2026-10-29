"""Lookup-related exceptions shared by begin/confirm/sync/revoke commands.

Reason to gather them in one place: if each command file defines its own
exception of the same name, they become different classes despite looking
alike, and the router's `except (...)` silently misses them — a real
mistake encountered while writing this leaf, hence standardized here.
"""

from __future__ import annotations


class ConnectionNotFoundError(Exception):
    pass


class CrossTenantConnectionAccessError(Exception):
    """Same principle as TRU-006 (item 73): deny access to another tenant's
    connection without leaking whether it exists (the caller maps to 404)."""
