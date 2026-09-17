"""U-3a (task-2630) -- `src/api/routers/assistant.py` domain exceptions -> ErrorCode.

`exception_registry_foundation.py` is already close to the P6.line_cap
300-line guard and follows the same split convention as
`EXCEPTION_MAP_FOUNDATION_MANDATES`/`_PERSONAL` (see that file's docstring).
"""

from __future__ import annotations

from src.api.contracts.error_codes import ErrorCode
from src.foundation.ai.assistant.adapters.anthropic_provider import ProviderNotConfiguredError
from src.foundation.ai.assistant.application.errors import AssistantFeatureDisabledError
from src.foundation.ai.assistant.domain.budget import BudgetExceededError

EXCEPTION_MAP_AI_ASSISTANT: list[tuple[type[Exception], ErrorCode]] = [
    # Flag off -- 404, as if the route did not exist (§U common DoD "fully inactive").
    (AssistantFeatureDisabledError, ErrorCode.RESOURCE_NOT_FOUND),
    # ANTHROPIC_API_KEY not configured -- 503, never pretend success.
    (ProviderNotConfiguredError, ErrorCode.DEPENDENCY_NOT_READY),
    # Tenant daily request cap exceeded -- 429 (do not encourage infinite
    # retries; retry guidance is the client's concern).
    (BudgetExceededError, ErrorCode.RATE_LIMIT_EXCEEDED),
]
