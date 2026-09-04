"""BT-1 — 백테스트 현실성 계약 v2 (`BacktestConfigV2`).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-1, §3.4(백테스트 현실성 계약), §9.5 BT-1,
107_contract_versioning_and_compatibility_standard_v1.0.md §3.3(MAJOR 변경).

`domain/models.py`(v1, `BacktestConfig`)는 109번 명세의 고정 bps 슬리피지·
수수료만 지원하는 계약이다. 이 v2는 체결 현실성 모델(슬리피지 3종·수수료
등급·지연·부분체결·주문유형·bar magnifier·펀딩/차입 비용·조정·캘린더)을
더한 신규 계약이라 v1을 고치지 않고 병존시킨다(107번 §3.3 — 필드 의미
변경은 새 버전 모듈, 기존 모듈은 불변).

BT-2~7(체결 모델)·BT-9(재현 키)가 이 계약에 1:1 의존하므로 §3.4 표 밖의
필드를 임의로 추가하지 않는다. `reproducibility_key`(재현 키, BT-9 책임)
산식 자체는 여기서 구현하지 않는다 — `BacktestConfigV2.canonical_json()`은
그 해시의 입력이 될 정준(canonical) 직렬화만 보장한다(결정론: 같은 값의
모델은 항상 같은 바이트열을 낸다).

모든 금액·비율·수수료는 `Decimal`이다(float 금지, 부동소수 오차가 체결
현실성 모델의 비교·누적 계산에 섞이는 것을 막는다).
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from src.foundation.market_data.contracts.v1 import Timeframe

SCHEMA_VERSION: Literal["backtest-v2"] = "backtest-v2"


class FixedSlippage(BaseModel):
    """봉마다 고정 bps만큼 불리한 체결가를 가정한다."""

    kind: Literal["fixed"] = "fixed"
    bps: Decimal = Field(ge=0)


class PercentSlippage(BaseModel):
    """체결가 대비 고정 비율(%)만큼 불리한 체결가를 가정한다."""

    kind: Literal["percent"] = "percent"
    pct: Decimal = Field(ge=0)


class VolumeImpactSlippage(BaseModel):
    """주문량이 봉 거래량에서 차지하는 참여율(participation)에 비례해
    시장충격을 가한다. `participation_cap`은 한 봉에서 소화 가능한 최대
    참여율(0 초과 1 이하) — 초과분 이월 처리는 체결 모델(BT-2~7)의 책임."""

    kind: Literal["volume_impact"] = "volume_impact"
    k: Decimal = Field(ge=0)
    participation_cap: Decimal = Field(gt=0, le=1)


SlippageModel = Annotated[
    FixedSlippage | PercentSlippage | VolumeImpactSlippage,
    Field(discriminator="kind"),
]


class VenueTierCommission(BaseModel):
    """거래소·등급별 메이커/테이커 수수료 + 최소 수수료(정액)."""

    venue: str
    maker_bps: Decimal = Field(ge=0)
    taker_bps: Decimal = Field(ge=0)
    min_fee: Decimal = Field(ge=0)


class PartialFillConfig(BaseModel):
    """한 봉에서 채울 수 있는 최대 참여율(0 초과 1 이하) — 초과 주문은
    체결 모델(BT-5)이 부분체결로 처리한다."""

    max_participation_pct: Decimal = Field(gt=0, le=1)


class OrderTypesConfig(BaseModel):
    """엔진이 허용하는 주문유형 스위치. 꺼진 유형으로 들어온 주문은 체결
    모델(BT-6)이 거부한다."""

    limit: bool
    stop: bool
    oco: bool
    trailing: bool


class CostsConfig(BaseModel):
    """`borrow_apr`은 공매도 등 차입 포지션에만 적용되므로 무차입 전략은
    `None`(적용 안 함)을 명시적으로 남긴다 — 0%로 조용히 채우지 않는다."""

    funding: bool
    borrow_apr: Decimal | None = Field(default=None, ge=0)


class AdjustmentsConfig(BaseModel):
    """분할·배당 조정을 각각 켜고 끈다(§3.4 `adjustments{splits, dividends}`)."""

    splits: bool
    dividends: bool


CalendarMode = Literal["session", "24x7"]


class BacktestConfigV2(BaseModel):
    """§3.4 백테스트 현실성 계약. 재생 1회 실행에 필요한 체결 현실성
    입력 전체를 고정(pin)한다 — 실행 도중 값을 바꾸지 않는다(105번 원칙)."""

    schema_version: Literal["backtest-v2"] = SCHEMA_VERSION
    slippage: SlippageModel
    commission: VenueTierCommission
    latency_ms: int = Field(ge=0)
    partial_fill: PartialFillConfig
    order_types: OrderTypesConfig
    magnifier_tf: Timeframe | None
    costs: CostsConfig
    adjustments: AdjustmentsConfig
    calendar: CalendarMode

    def canonical_json(self) -> str:
        """`reproducibility_key`(BT-9) 입력용 정준 직렬화 — 키 정렬·구분자
        고정으로 같은 값이면 항상 같은 바이트열을 낸다. 해시 계산 자체는
        BT-9(`domain/reproducibility.py`)의 책임이며 여기서는 하지 않는다."""

        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
