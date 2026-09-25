"""Lookup exceptions shared by issue/revoke/authorize.

Following the convention connections/mandates already established (a single
`NotFoundError`/`CrossTenant...Error` pair defined once, not redefined per
use case) -- [[src/foundation/connections/application/errors.py]].
"""

from __future__ import annotations


class AgentTokenNotFoundError(Exception):
    pass


class CrossTenantAgentTokenAccessError(Exception):
    """A token_id owned by another tenant is rejected without even leaking
    its existence (the caller maps this to a 404) -- same principle as
    connections' CrossTenantConnectionAccessError."""
