"""FD-13.11(신설) — 지갑(크레딧) API 요청 바디 스키마."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class TopupRequestBody(BaseModel):
    amount: Decimal
