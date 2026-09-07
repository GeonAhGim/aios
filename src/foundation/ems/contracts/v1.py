"""EM-1 — EMS(집행: 라우팅·알고리즘·TCA) 계약 v1.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §2 모듈표, §3 계약 요지,
§9 EM-1.

`AlgoSpec`은 OMS `src/services/oms/contracts/v1_commands.py`의 `AlgoRequest`를
대체하는 정본이다(감사 2026-09-06). `ICEBERG`는 `kind`의 한 값으로 흡수됐고,
`AlgoRequest.size_jitter_pct`/`time_jitter_pct`는 EM-A3(결정론: 같은 스냅샷·
같은 스펙 → 같은 계획)와 충돌해 여기 계약에는 없다 — 대신 `seed`만으로
재현 가능한 슬라이스 계획을 만든다(같은 seed → 같은 계획). 기존
`AlgoRequest`는 이 리프에서 삭제하지 않는다(호출부 이관은 EM-3 몫).

`src/foundation/entities/contracts/v1.py`(FA-1) 관례를 따른다: 이 모듈은
`domain/`을 import하지 않고, 도메인 계층(EM-2 이후)이 이 계약만 import한다
(71번 §4). 에러 코드는 값만 여기서 정의하고, 실제 예외 클래스는 이 코드를
쓰는 도메인 리프(EM-2/EM-7 등)의 책임이다(FA-1 `EntityErrorCode`와 동일 원칙).
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, BeforeValidator, Field, model_validator
from starlette import status

from src.data.models.trading import OrderSide, OrderStatus

SCHEMA_VERSION: Literal["v1"] = "v1"

TERMINAL_ORDER_STATUSES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.FILLED,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
        OrderStatus.FAILED,
    }
)
"""EM-A4 — 부모가 이 상태면 신규 자식 생성 금지(`EM_PARENT_TERMINAL`). 01번
공유접점 `OrderStatus`를 그대로 쓴다(EMS가 독자 상태 축을 만들지 않는다) —
`UNKNOWN`은 8.3 원칙대로 터미널이 아니다."""


def _reject_float(value: object) -> object:
    """금액·수량·bps 필드의 float 입력을 거부한다(정밀도 손실 방지).

    pydantic v2 기본은 `Decimal` 필드에 float을 그대로 허용해(예:
    `Decimal(0.1)`이 아니라 `Decimal(str(0.1))`이라도) 이진부동소수 오차가
    스며들 여지를 남긴다 — 이 프로젝트는 `int`/`str`/`Decimal` 입력만
    허용하고 float은 타입 자체로 거부한다(문자열 경유만 허용).
    """
    if isinstance(value, float):
        raise ValueError("float은 허용되지 않습니다 — Decimal 또는 문자열로 전달하세요.")
    return value


StrictDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]


class EmsErrorCode(str, Enum):
    """§3 에러 taxonomy — 문자 단위로 스펙과 일치해야 한다(스냅샷 테스트)."""

    ALGO_CONSTRAINT = "EM_ALGO_CONSTRAINT"  # 400 — AlgoSpec 제약 위반(범위 밖 kind 등)
    NO_ROUTE = "EM_NO_ROUTE"  # 409 — 벤처 장애로 대체 라우팅 불가
    PARENT_TERMINAL = "EM_PARENT_TERMINAL"  # 409 — 터미널 부모에 신규 자식 시도
    PARTICIPATION_EXCEEDED = "EM_PARTICIPATION_EXCEEDED"  # 409 — 참여율 상한 초과


HTTP_STATUS: dict[EmsErrorCode, int] = {
    EmsErrorCode.ALGO_CONSTRAINT: status.HTTP_400_BAD_REQUEST,
    EmsErrorCode.NO_ROUTE: status.HTTP_409_CONFLICT,
    EmsErrorCode.PARENT_TERMINAL: status.HTTP_409_CONFLICT,
    EmsErrorCode.PARTICIPATION_EXCEEDED: status.HTTP_409_CONFLICT,
}


class AlgoKind(str, Enum):
    """§3 `AlgoSpec.kind` — 표 밖 값(예: `sniper`)은 pydantic이 거부한다."""

    TWAP = "twap"
    VWAP = "vwap"
    POV = "pov"
    IS = "is"
    ICEBERG = "iceberg"


class AlgoSpec(BaseModel):
    """§3 — OMS `AlgoRequest`를 대체하는 정본. `seed`만으로 결정론적
    슬라이스 계획을 재현한다(EM-A3) — 지터 비율 필드는 두지 않는다."""

    kind: AlgoKind
    start: AwareDatetime
    end: AwareDatetime
    max_participation_pct: StrictDecimal = Field(gt=0, le=100)
    slice_interval_sec: int = Field(gt=0)
    urgency: StrictDecimal = Field(ge=0, le=1)
    limit_price: StrictDecimal | None = Field(default=None, gt=0)
    seed: int
    schema_version: Literal["v1"] = SCHEMA_VERSION

    @model_validator(mode="after")
    def _check_window(self) -> AlgoSpec:
        if self.end <= self.start:
            raise ValueError("end는 start보다 뒤여야 합니다.")
        return self


class ParentOrderConstraints(BaseModel):
    """EM-A2 — 알고리즘이 완화할 수 없는 상한. 부모를 통과시킨 리스크·
    컴플라이언스 게이트가 산출한 값을 그대로 옮겨 담을 뿐, 여기서 재계산하지
    않는다(재계산은 각 게이트의 책임, I-09). 구체 필드 구성은 EM-6(라우팅)·
    EM-7(알고 가드)이 실제로 소비하며 확정한다 — 여기서는 두 리프가 이미
    합의된 상한(참여율)만 고정한다."""

    max_participation_pct: StrictDecimal = Field(gt=0, le=100)
    schema_version: Literal["v1"] = SCHEMA_VERSION


class ParentOrder(BaseModel):
    """§2 계약표 — `ParentOrder{parent_id, instrument_id, side, qty, algo,
    constraints, fund_id, portfolio_id}`. `arrival_ts`는 §3 "TCA 벤치마크
    기준시각은 부모 주문 arrival_ts로 고정"을 위해 필수로 추가했다(표에는
    없지만 §3 산문 규칙이 요구하는 필드 — FA-1 `closed_at` 추가와 동일 근거).
    `status`는 EM-A4(`EM_PARENT_TERMINAL`) 판정에 쓰인다."""

    parent_id: UUID
    instrument_id: str
    side: OrderSide
    qty: StrictDecimal = Field(gt=0)
    algo: AlgoSpec
    constraints: ParentOrderConstraints
    fund_id: UUID
    portfolio_id: UUID
    arrival_ts: AwareDatetime
    status: OrderStatus = OrderStatus.CREATED
    schema_version: Literal["v1"] = SCHEMA_VERSION


class ChildOrder(BaseModel):
    """§2 계약표 — `ChildOrder{child_id, parent_id, slice_seq, ...}`.
    `order_id`는 `submit_order`를 통과해 실제 주문이 생성된 뒤에만 채워진다
    (§3 "자식 주문은 예외 없이 submit_order를 통과한다") — `None`이면 아직
    계획 단계이고 어떤 어댑터에도 도달하지 않은 슬라이스다. 체결·상태는
    01번 공유접점 `Order`(=`order_id`로 조회)가 유일한 출처이며 여기서
    중복 보관하지 않는다(SSOT)."""

    child_id: UUID
    parent_id: UUID
    slice_seq: int = Field(ge=0)
    instrument_id: str
    side: OrderSide
    planned_qty: StrictDecimal = Field(gt=0)
    scheduled_at: AwareDatetime
    limit_price: StrictDecimal | None = Field(default=None, gt=0)
    order_id: UUID | None = None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class RouteDecision(BaseModel):
    """§3 — `RouteDecision{venue, reason_codes, expected_cost_bps}`.
    `reason_codes`는 예시(`BEST_FEE`/`DEEPEST_BOOK`/`ONLY_VENUE`)이지 폐쇄
    집합이 아니다(§3 "예:") — 자유 문자열로 두고 닫힌 enum으로 제한하지
    않는다."""

    venue: str
    reason_codes: list[str] = Field(min_length=1)
    expected_cost_bps: StrictDecimal
    schema_version: Literal["v1"] = SCHEMA_VERSION


class TcaResult(BaseModel):
    """§3 — `TcaResult{arrival_bps, vwap_bps, impact_bps, fees_bps,
    opportunity_bps}`. 분해 합이 총비용과 같음을 강제하는 검증(EM-A5/§8)은
    `domain/tca/decomposition.py`(EM-13)의 책임이다 — 이 계약은 형태만
    정의한다."""

    arrival_bps: StrictDecimal
    vwap_bps: StrictDecimal
    impact_bps: StrictDecimal
    fees_bps: StrictDecimal
    opportunity_bps: StrictDecimal
    schema_version: Literal["v1"] = SCHEMA_VERSION
