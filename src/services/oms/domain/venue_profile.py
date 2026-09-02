"""거래소 capability 프로파일 모델(L4 명세 §3.2, R14).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-A/§3.2, §9 L4-04.

R14 — "다거래소 capability gating" — KIS/NH/Bitget이 지원하지 않는 주문
유형·TIF·client id 정책을 명시적으로 거부한다(감사: 이 검증 자체가
없었음).

`TimeoutBudget`/`MarketHours`는 이 파일에서 자체 정의한다 — domain 계층은
I/O가 없어야 하므로(§2-A 표 "도메인은 외부 I/O 없음") `exchanges/common/
http_policy.py`(L4-11, 아직 없음)에 의존하지 않는다. L4-11이 실제로
만들어지면 그쪽 `TimeoutBudget`은 이 도메인 타입과 구조가 같은 별도
정의이거나, 이 타입을 그대로 재사용하는 방향으로 정리한다(그때 판단).

편차(해석): §2-A는 `assert_supported`가 `UnsupportedVenueFeatureError(code)`를
던진다고 적지만, §3.4 에러 taxonomy표는 "UNSUPPORTED_TYPE"/"UNSUPPORTED_TIF"를
`OrderValidationError`(`OMS_VALIDATION_*`) 계열로 분류한다. §3.4를
권위 있는 taxonomy로 보고 `OrderValidationError`를 던진다 — `errors.py`의
클래스 계층과도 일관적이다(핵심 판단력=주문 검증 실패라는 성격이 같음).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from src.data.models.base import AssetClass
from src.data.models.trading import OrderType
from src.services.oms.contracts.v1_commands import SubmitOrderCommand
from src.services.oms.domain.errors import OrderValidationError


@dataclass(frozen=True)
class TimeoutBudget:
    connect: float = 2.0
    read: float = 5.0
    total: float = 8.0


@dataclass(frozen=True)
class MarketHours:
    """단순 일일 개장/폐장(로컬 거래소 시간대, HH:MM) — 공휴일 캘린더는
    이 리프의 스콥 밖(향후 exchanges 계층이 별도로 다룸)."""

    open_time: str
    close_time: str
    timezone: str


class VenueCapabilityProfile(BaseModel):
    venue: str
    asset_classes: list[AssetClass]
    order_types: set[OrderType]
    time_in_force: set[str]
    supports_client_order_id: bool
    client_order_id_max_len: int
    client_order_id_charset: str
    id_policy: Literal["STABLE", "DAILY_SEQUENCE"]
    supports_modify: bool
    supports_cancel: Literal["YES", "UNVERIFIED"]
    supports_ws_orders: bool
    supports_batch: bool
    price_tick: dict[str, Decimal]
    qty_lot: dict[str, Decimal]
    min_notional: dict[str, Decimal]
    rate_limits: dict[str, tuple[int, int]]  # group -> (per_sec, burst)
    submit_timeout: TimeoutBudget
    query_timeout: TimeoutBudget
    market_hours: MarketHours | None
    max_open_orders_per_symbol: int
    verified: Literal["LIVE_VERIFIED", "DOC_ONLY", "ESTIMATED"]  # §10 정직 표기

    model_config = {"arbitrary_types_allowed": True}


def assert_supported(profile: VenueCapabilityProfile, cmd: SubmitOrderCommand) -> None:
    if cmd.asset_class not in profile.asset_classes:
        raise OrderValidationError(
            "UNSUPPORTED_TYPE",
            f"{profile.venue}는 자산군 {cmd.asset_class.value}를 지원하지 않습니다.",
        )
    if cmd.order_type not in profile.order_types:
        raise OrderValidationError(
            "UNSUPPORTED_TYPE",
            f"{profile.venue}는 주문유형 {cmd.order_type.value}를 지원하지 않습니다.",
        )
    if cmd.time_in_force not in profile.time_in_force:
        raise OrderValidationError(
            "UNSUPPORTED_TIF",
            f"{profile.venue}는 TIF {cmd.time_in_force}를 지원하지 않습니다.",
        )
