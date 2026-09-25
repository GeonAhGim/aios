"""AI-17 -- `StrategyProposal` read-path lookup exception.

Same "wrong tenant reads as missing" posture as
`gateway/application/errors.py::AgentTokenNotFoundError`
([[src/foundation/ai/gateway/application/errors.py]]) --
`PostgresProposalRepository.get_for_tenant` already collapses "does not
exist" and "exists but owned by another tenant" into a single `None` (that
adapter's own docstring), so a single exception is enough here (unlike the
gateway's token lookup, no second exception class distinguishing the two
cases is needed).
"""

from __future__ import annotations


class ProposalNotFoundError(Exception):
    pass
