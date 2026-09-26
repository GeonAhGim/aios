"""U-8 (task-8105) -- `src/api/routers/risk_coach.py` domain exceptions -> ErrorCode.

`exception_registry_foundation.py` is already over the P6.line_cap 300-line
guard and follows the same split convention as
`EXCEPTION_MAP_AI_ASSISTANT`/`_FOUNDATION_MANDATES`/`_FOUNDATION_PERSONAL`
(see that file's docstring).
"""

from __future__ import annotations

from src.api.contracts.error_codes import ErrorCode
from src.foundation.risk_coach.flags import RiskCoachFeatureDisabledError

EXCEPTION_MAP_RISK_COACH: list[tuple[type[Exception], ErrorCode]] = [
    # Flag off -- 404, as if the route did not exist (§U common DoD "fully inactive").
    (RiskCoachFeatureDisabledError, ErrorCode.RESOURCE_NOT_FOUND),
]
