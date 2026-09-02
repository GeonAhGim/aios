# 01. 데이터 모델 (Pydantic) — v1.4

> **v1.4(2026-08-28) = 다자산군(Multi-Asset-Class) 확장 라운드.** ADR-2026-08-28
> 반영 — `AssetClass`/`OptionType` 신설(§1.0), `Order`/`Position`에 파생상품
> 필드(자산군, 옵션 행사가·만기, 선물 만기월·계약승수, 기초자산) 추가. 코인
> 현물뿐 아니라 국내/해외 주식·선물·옵션·ETN/ETF까지 표현 가능한 공통
> 상위집합으로 확장 — 실제 어떤 거래소가 무엇을 지원하는지는 02번
> `ExchangeCapability.supported_asset_classes`가 선언하고, 미지원 조합은
> Validator(03번)가 거부한다(capability-gated 원칙, ADR 본문 참조).

> **v1.3(2026-08-10) = "모든 문서 실제 구현가능성 검증" 라운드.** `SecretBundle`
> 신설 — 03번 §3.1(Loader.load_env_secrets)이 반환 타입으로 계속 참조해왔지만
> 실제 필드 정의가 어디에도 없었음(16번 session.py가 `get_settings()`라는
> 존재하지 않는 함수를 참조하다가 발견됨). 07번 §7.3 `.env.example` 전체
> 목록과 1:1 대응하는 필드로 구성, 마스킹 원칙(07번 §7.1) 포함.

> **v1.1(2026-08-10) = "0번부터 재검토" 라운드 — 번호 충돌 정정.** §1.1~1.7이
> 정책문서(docx) 1장(1.1~1.5, 프로젝트 정의)과 번호가 겹치는 것을 발견 —
> 06~11/13/15~17번과 동일 라운드에 동일 조치.
> **v1.2(2026-08-10)**: ADR-2026-08-10-C(Order/Position에 execution_id 필드
> 추가) 실제 병합 — 이전 라운드에 패치 파일(patch-01-data-models-execution-id.md)
> 로만 존재하고 이 문서 본문에는 실제로 반영되지 않았던 것을 "산출물 최종
> 점검" 단계에서 발견해 완결.

> 근거: AIOS 문서 4.3(Task 스키마), 9.11(FSM Strategy 스키마), 8장(Market/Trading 데이터)
> 상태: SCAFFOLD-READY (전부 순수 데이터 클래스, 실행 로직 없음)

```python
# src/data/models/base.py
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ProvenanceStatus(str, Enum):
    """4.6-A Memory 검증 상태 — Task/Memory/Strategy 공통 사용"""
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    DISPUTED = "DISPUTED"
```

## §1.0 AssetClass / OptionType (v1.4 신설 — ADR-2026-08-28 다자산군 확장)

```python
# src/data/models/base.py 추가
class AssetClass(str, Enum):
    """AIOS가 표현할 수 있는 전체 자산군 상위집합. 개별 거래소가 이 중
    무엇을 실제로 지원하는지는 02번 ExchangeCapability.supported_asset_classes가
    선언한다 — 이 enum에 있다고 모든 거래소에서 거래 가능하다는 뜻이 아니다."""
    CRYPTO = "CRYPTO"                       # 코인 현물/파생 (Bitget 등)
    KR_EQUITY = "KR_EQUITY"                 # 국내주식
    KR_ETF = "KR_ETF"                       # 국내 ETF
    KR_ETN = "KR_ETN"                       # 국내 ETN
    KR_FUTURES = "KR_FUTURES"               # 국내 선물
    KR_OPTION = "KR_OPTION"                 # 국내 옵션
    US_EQUITY = "US_EQUITY"                 # 해외주식(미국 우선)
    US_ETF = "US_ETF"                       # 해외 ETF
    US_ETN = "US_ETN"                       # 해외 ETN
    OVERSEAS_FUTURES = "OVERSEAS_FUTURES"   # 해외 선물
    OVERSEAS_OPTION = "OVERSEAS_OPTION"     # 해외 옵션(Draft — 실제 지원 여부 미확정)


class OptionType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"
```

- **의도적으로 만들지 않는 것(Draft)**: 별도 `Instrument`/`InstrumentSpec` 참조
  테이블. 옵션 행사가·만기, 선물 만기월 등은 `Order`/`Position`에 직접
  Optional 필드로 둔다(§1.4) — 심볼 마스터/옵션체인 등 정규화가 실제로
  필요해지는 시점(해당 자산군 착수 시)에 재검토한다(17.9-A 과잉설계 방지
  원칙과 동일 정신).

## §1.1 AIOSTask (4.3 스키마의 Pydantic 구현)

```python
# src/data/models/task.py
class TaskStatus(str, Enum):
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class AIOSTask(BaseModel):
    """4.3 JSON Schema의 1:1 Pydantic 구현. 16.2 Capability Token의 task_id와 연동."""
    task_id: UUID = Field(default_factory=uuid4)
    parent_task_id: Optional[UUID] = None
    objective: str
    assigned_agent: str  # 5장 Agent Registry의 agent_id 참조
    required_permission_level: int = Field(ge=0, le=6)  # 4.5 Permission Level
    status: TaskStatus = TaskStatus.PENDING
    input_payload: dict = Field(default_factory=dict)
    output_result: Optional[dict] = None
    retry_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    class Config:
        use_enum_values = True
```

## §1.2 FSMStrategyConfig (9.11 스키마의 Pydantic 구현)

```python
# src/data/models/strategy_fsm.py
class FSMState(str, Enum):
    IDLE = "IDLE"
    BUY_ORDER_PENDING = "BUY_ORDER_PENDING"
    HOLDING = "HOLDING"
    SELL_ORDER_PENDING = "SELL_ORDER_PENDING"
    STOP_LOSS = "STOP_LOSS"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"


class FSMTransition(BaseModel):
    from_state: FSMState
    to_state: FSMState
    condition: str  # 조건식 문자열 — 평가 로직은 FROZEN Zone(Strategy Engine)에서 구현


class FSMStrategyConfig(BaseModel):
    """9.11 — 전략은 반드시 이 FSM 구조로만 정의한다 (무한루프/상태꼬임 방지, 9.11 원문 참조)."""
    strategy_id: str
    version: str  # 예: "v1.4"
    target_asset: str
    market: str  # "crypto" | "kr_stock" 등
    exchange: str
    initial_state: FSMState = FSMState.IDLE
    states: list[FSMState]
    transitions: list[FSMTransition]

    # 9.4 Strategy Definition 추가 필드
    author_agent: str  # 어떤 Agent가 생성했는지 (5.4 Strategy Research Agent)
    memory_provenance: list[UUID] = Field(default_factory=list)
    """10차 레드팀 반영(16장 인접) — 이 전략 생성에 참조된 Memory 항목 ID들.
    4.6-A Memory-Strategy 출처 연결 원칙 구현. 9.5 검증 시 이 필드의 다양성을 확인한다."""
```

## §1.3 Market Data 모델

```python
# src/data/models/market_data.py
class Ticker(BaseModel):
    symbol: str
    exchange: str
    price: Decimal
    bid: Decimal
    ask: Decimal
    volume_24h: Decimal
    timestamp: datetime
    source_type: str  # "primary" | "reference" — 8.1-A 다중소스 교차검증용


class Candle(BaseModel):
    symbol: str
    exchange: str
    timeframe: str  # "1m", "5m", "1h" 등
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    open_time: datetime
    close_time: datetime


class OrderBookLevel(BaseModel):
    price: Decimal
    quantity: Decimal


class OrderBook(BaseModel):
    symbol: str
    exchange: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp: datetime
```

## §1.4 Trading 모델

```python
# src/data/models/trading.py
class OrderStatus(str, Enum):
    """8.3 Order State Machine 1:1 구현"""
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"  # 8.3 원칙: UNKNOWN을 실패로 단정하지 않는다


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    # TWAP/VWAP 등은 8.3-A Execution Strategy 계층에서 분할 주문으로 구현(단일 Order 타입 아님)


class Order(BaseModel):
    order_id: UUID = Field(default_factory=uuid4)
    exchange_order_id: Optional[str] = None  # 7.5 주문 멱등성 — 거래소측 ID 별도 추적
    client_order_id: str  # 멱등성 키. 재전송 시에도 동일 값 사용
    strategy_id: str
    strategy_version: str
    execution_id: Optional[int] = None
    # ADR-2026-08-10-C(v1.1 병합) — FD-16(전략 실행 제어판) 신설로 추가.
    # 이 주문이 어느 실행 인스턴스(strategy_executions.id) 소속인지 추적.
    # None 허용: FD-8 FROZEN 판단 계층이 실행 컨텍스트 없이 직접 호출되는
    # 테스트/시뮬레이션 경로가 있을 수 있어 NOT NULL로 강제하지 않음
    # (04번 DB도 NULL 허용). 하위호환 확장이며 상태 전이 규칙 자체는 불변
    # — 공유접점문서 §2.3 동결계약 위반 아님(ADR 본문 참조).
    symbol: str
    exchange: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Optional[Decimal] = None  # MARKET 주문은 None
    status: OrderStatus = OrderStatus.CREATED
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Optional[Decimal] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # 8.6-A-2 Anti-Front-Running — 청산 실행 시 슬라이스 정보 (일반 주문은 None)
    execution_slice_id: Optional[str] = None
    is_liquidation: bool = False

    # v1.4(ADR-2026-08-28) 다자산군 확장 — asset_class는 필수(기본값 없음,
    # 침묵 오분류 방지). 나머지 파생상품 필드는 옵션/선물 주문에서만 채워지고
    # 크립토/현물·주식 주문은 전부 None 유지.
    asset_class: "AssetClass"
    option_type: Optional["OptionType"] = None          # 옵션 주문만
    strike_price: Optional[Decimal] = None              # 옵션 주문만
    expiry_date: Optional[datetime] = None              # 옵션/선물 주문만(만기)
    contract_multiplier: Optional[Decimal] = None       # 선물/옵션 주문만(계약승수)
    underlying_symbol: Optional[str] = None             # 선물/옵션 주문만(기초자산)


class Position(BaseModel):
    symbol: str
    exchange: str
    strategy_id: str
    execution_id: Optional[int] = None  # ADR-2026-08-10-C(v1.1 병합), Order와 동일 근거
    quantity: Decimal
    average_entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    leverage: Decimal = Decimal("1")
    margin: Optional[Decimal] = None
    entry_time: datetime
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # v1.4(ADR-2026-08-28) 다자산군 확장 — Order와 동일 원칙(asset_class 필수,
    # 나머지는 파생상품 포지션에서만 채워짐).
    asset_class: "AssetClass"
    option_type: Optional["OptionType"] = None
    strike_price: Optional[Decimal] = None
    expiry_date: Optional[datetime] = None
    contract_multiplier: Optional[Decimal] = None
    underlying_symbol: Optional[str] = None


class SecretBundle(BaseModel):
    """FD-1.1/03번 §3.1(Loader.load_env_secrets)가 반환하는 타입 — "모든 문서
    실제 구현가능성 검증" 라운드에서 03번이 타입으로만 참조하고 정의가
    없었음을 발견해 신설. 07번 §7.3 `.env.example` 전체 목록과 1:1 대응."""
    database_url: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    credential_encryption_key: str  # FD-12.1 거래소 자격증명 + FD-11.5 출금 화이트리스트 암호화 공용
    bitget_api_key: str
    bitget_api_secret: str
    kis_app_key: str
    kis_app_secret: str
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    fcm_server_key: str | None = None
    apns_key_id: str | None = None

    def __repr__(self) -> str:
        """FD-1.1/07번 §7.1 마스킹 원칙 — 어떤 필드도 평문 노출 금지."""
        return f"SecretBundle(<{len(self.__class__.model_fields)} fields, masked>)"

    __str__ = __repr__


class AccountBalance(BaseModel):
    """v1.4(2026-08-28) 확정 — 작업트리 6번(BitgetAdapter) 구현 중 실제
    Bitget 잔고 API(`GET /api/v2/spot/account/assets`)를 조사하며 발견:
    이 모델을 Money(currency: Currency)로 바꾸면 안 된다. Currency enum은
    USDT/KRW(결제·표시 통화)만 정의하는데, 실제 계좌 잔고는 BTC/ETH/SOL 등
    임의 코인 보유량이라 Currency로 표현할 수 없다. Money의 목적은 "서로
    다른 결제통화를 실수로 합산하지 못하게 막는 것"(11번 §11.1)이지 "이
    필드가 어떤 자산인지 표시하는 것"이 아니다 — 후자는 이미 `asset: str`
    필드가 담당한다. 그래서 이 모델만 Decimal을 유지한다(Order/Position의
    가격·손익 필드는 실제로 결제통화 금액이므로 Money가 맞다 — 11번 §11.1
    원칙 자체는 그대로 유효, 이 모델에만 예외적으로 적용 안 될 뿐)."""

    exchange: str
    asset: str
    total: Decimal
    available: Decimal
    used_margin: Decimal = Decimal("0")
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

## §1.5 Memory 모델 (4.6-A Provenance Tracking)

```python
# src/data/models/memory.py
class MemoryType(str, Enum):
    SHORT_TERM = "SHORT_TERM"
    WORKING = "WORKING"
    LONG_TERM = "LONG_TERM"
    EPISODIC = "EPISODIC"
    DECISION = "DECISION"
    FAILURE = "FAILURE"
    PERFORMANCE = "PERFORMANCE"


class MemoryEntry(BaseModel):
    """4.6-A — 모든 Memory 항목은 출처·신뢰도·검증상태를 가진다."""
    memory_id: UUID = Field(default_factory=uuid4)
    memory_type: MemoryType
    content: dict
    source_agent: str
    source_task_id: Optional[UUID] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    status: ProvenanceStatus = ProvenanceStatus.UNVERIFIED
    verified_by: Optional[str] = None  # 검증한 Agent (Auditor 등)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    verified_at: Optional[datetime] = None
```

## §1.6 Decimal ↔ JSONB 직렬화 원칙 (v3.1 신설, 09번 §9.1 #3 반영)

`AIOSTask.input_payload`, `MemoryEntry.content`, `audit_log.decision_data` 등 `dict`/JSONB로 저장되는 필드에 `Decimal` 값이 섞이면, Python 표준 `json` 모듈이 기본적으로 직렬화하지 못해 런타임 오류가 난다.

```python
# src/data/models/serialization.py
import json
from decimal import Decimal

class DecimalSafeEncoder(json.JSONEncoder):
    """Decimal을 문자열로 직렬화 — float 변환 금지(정밀도 손실 방지, 8.2-B 수치 정확성과 직결)."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)

# 모든 dict→JSONB 저장 경로는 이 인코더를 통과시킨다.
# 역직렬화 시에는 해당 필드가 Decimal이어야 함을 아는 호출부가 명시적으로 Decimal(value)로 복원한다
# (JSONB 자체는 타입 정보를 보존하지 않으므로 자동 왕복 변환은 없다).
```

원칙: **Pydantic 모델 필드는 `Decimal`을 유지**하고(정밀도 보존), **JSONB에 넣기 직전에만** 위 인코더로 문자열화한다 — 모델 정의 자체를 `str`로 바꾸지 않는다.

## §1.7 시간대(Timezone) 원칙

모든 `datetime` 필드는 **UTC 기준 tz-aware**로 저장한다. `Field(default_factory=datetime.utcnow)`는 Python 3.12+에서 deprecated 경고가 발생하므로, 실제 구현 시 `datetime.now(timezone.utc)`로 교체한다(이 문서의 예시 코드는 가독성을 위해 축약형을 사용했다). KIS의 `market_hours`(Asia/Seoul)와 UTC 저장 시각 간 변환은 표시 계층의 책임이며, 저장 계층은 항상 UTC를 유지한다.

