"""U-8 (task-8105) -- `POST /risk-coach/position-size`.

Spec: ADR-2026-09-09-B Decision C U-8 ("position sizing calculator, reuse the
R- engine"), ADR-2026-09-26-A (cloud-only staged rollout, feature flag off by
default).

Read-only delegation to `src.core.portfolio.sizing.selector.size_for` -- this
router does not compute anything itself (no duplicate context, §C). No
order/ledger writes happen anywhere in this file.

When `FF_U8_RISK_COACH` is off, the endpoint responds with
`RiskCoachFeatureDisabledError` (404) -- the same shape as
`AssistantFeatureDisabledError` in `assistant.py`. Domain exceptions
(`SizingResultTamperedError`/`UnknownSizingMethodError`) are not caught here
-- the global handler maps them via `EXCEPTION_MAP` (zero raw
HTTPException).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_current_user
from src.core.portfolio.config import PortfolioConfig
from src.core.portfolio.sizing import SizingResult
from src.core.portfolio.sizing.selector import size_for
from src.core.portfolio.state_input import PortfolioStateInput
from src.foundation.risk_coach.flags import require_flag_enabled
from src.services.auth_service import User

router = APIRouter(tags=["risk-coach"])


class PositionSizeRequest(BaseModel):
    config: PortfolioConfig
    state_input: PortfolioStateInput


@router.post("/position-size", response_model=ApiResponse[SizingResult])
async def post_position_size(
    body: PositionSizeRequest,
    _user: User = Depends(get_current_user),
    _flag: None = Depends(require_flag_enabled),
) -> ApiResponse[SizingResult]:
    result = size_for(body.config, body.state_input)
    return ok(result)
