"""AI-17 (task-2652) -- `src/api/routers/ai.py` domain exceptions -> ErrorCode.

`exception_registry_foundation.py` is already close to the P6.line_cap
300-line guard and follows the same split convention as
`EXCEPTION_MAP_AI_ASSISTANT`/`_FOUNDATION_MANDATES`/`_FOUNDATION_PERSONAL`
(see that file's docstring).

Token/proposal cross-tenant access folds into the same `RESOURCE_NOT_FOUND`
as the plain "does not exist" case -- the non-disclosure-of-existence
posture already applied to connections/charting/positions elsewhere in
`exception_registry_foundation.py` (a token or proposal owned by another
tenant must not be distinguishable from one that never existed).
"""

from __future__ import annotations

from src.api.contracts.error_codes import ErrorCode
from src.foundation.ai.factory.application.errors import ProposalNotFoundError
from src.foundation.ai.gateway.application.errors import (
    AgentTokenNotFoundError,
    CrossTenantAgentTokenAccessError,
)
from src.foundation.ai.gateway.domain.token_rules import TokenRuleError
from src.foundation.experiments.application.query import ExperimentNotFoundError

EXCEPTION_MAP_AI_GATEWAY: list[tuple[type[Exception], ErrorCode]] = [
    (AgentTokenNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (CrossTenantAgentTokenAccessError, ErrorCode.RESOURCE_NOT_FOUND),
    (ProposalNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (ExperimentNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    # Empty scope list at issue/rotate time (`domain/token_rules.py::issue_scopes`)
    # -- a client-input error, not a server fault. `ScopeEscalationError` is a
    # subclass and folds into the same code (unreachable via this router today
    # since `scopes` is already a `list[Scope]` on the wire, but mapped
    # defensively rather than left to fall through to INTERNAL_ERROR).
    (TokenRuleError, ErrorCode.VALIDATION_INVALID_FIELD),
]
