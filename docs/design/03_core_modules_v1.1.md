# 03. src/core/ 8개 모듈 함수 시그니처 — v1.2

> **v1.2(2026-09-01) = FD-4/FD-8 PAPER 모드 실동작 구현 완료.** §3.5~3.8
> 4개 클래스 전부 실제 코드로 채워졌다(FROZEN-INTERFACE-ONLY 상태 종료) —
> `src/core/strategy/engine.py`, `src/core/portfolio/engine.py`,
> `src/core/risk/engine.py`, `src/core/executor/executor.py`. 아래 코드
> 블록은 여전히 03번 원문 시그니처(감사 대상 계약)를 보여주지만, 실제
> 구현은 `execution_id`/`fsm_state`/`user_id` 등 키워드 전용 인자를
> 추가로 받는다(위치 인자는 그대로 유지 — ADR-2026-08-29-E "인터페이스
> 자체는 변경하지 않는다" 원칙 준수, 상세 근거는 각 파일 docstring 참조).
> FD-4(주문 전송 계층)도 이 참에 `src/services/order_service/`로 함께
> 구현됐다(FD-4.2/4.3/4.4/4.5). 실행 루프 오케스트레이션은
> `src/services/execution_loop/`(FD-8 번호 없음, 배선 코드) 참조.
> Bitget Demo 계정(PAPER 실거래 왕복) 자체는 여전히 실계정 미확보로
> 검증 못 함 — `FakeExchangeAdapter` 기반 통합테스트로 파이프라인
> 전체(멱등성·DB영속화·이벤트발행·FSM전이·8개 리스크 지표)를 검증했다.

> **v1.1(2026-08-10) = "0번부터 재검토" 라운드 — 번호 충돌 정정.** §3.1~3.9가
> 정책문서(docx) 3장(3.1~3.5+, AIOS 자율성 5단계 등 — 특히 정책문서 3.4는
> "자율성 Hard Gate"인데 이 문서 §3.4는 "Scanner"로 완전히 다른 내용)과
> 번호가 겹치는 것을 발견 — 01/02번과 동일 라운드에 동일 조치.

> 근거: AIOS 문서 6.2~6.12(모듈 책임), 8장(Trading Engine 파이프라인)
> Loader/Parser/Validator/Scanner = SCAFFOLD-READY
> Strategy/Portfolio/Risk/Executor = FROZEN-PAPER-ONLY (ADR-2026-08-29-E — PAPER 모드 한정 구현 가능, LIVE는 15.6-D 조건 2 충족 전까지 하드 차단)

## §3.1 Loader — `src/core/loader/`

```python
# STATUS: SCAFFOLD-READY
from pathlib import Path
from src.data.models.strategy_fsm import FSMStrategyConfig


class Loader:
    """6.5 — 데이터를 해석하거나 투자판단을 하지 않는다. 읽기만 한다."""

    def load_config(self, path: Path) -> dict: ...

    def load_strategy_file(self, path: Path) -> FSMStrategyConfig: ...

    def load_env_secrets(self) -> "SecretBundle":
        """7.4 원칙 — .env 파일 로드. 이 함수의 반환값은 절대 로그에 출력되지 않도록
        호출부에서 SecretBundle.__repr__을 마스킹 처리해야 한다."""
        ...
```

## §3.2 Parser — `src/core/parser/`

```python
# STATUS: SCAFFOLD-READY
from src.data.models.market_data import Ticker, Candle, OrderBook


class Parser:
    """6.6 — Raw API 응답을 Internal Data Model로 변환. 검증은 하지 않는다(Validator 책임)."""

    def parse_ticker(self, raw: dict, exchange: str) -> Ticker: ...

    def parse_candles(self, raw: list[dict], exchange: str, timeframe: str) -> list[Candle]: ...

    def parse_orderbook(self, raw: dict, exchange: str) -> OrderBook: ...
```

## §3.3 Validator — `src/core/validator/`

```python
# STATUS: SCAFFOLD-READY
from src.data.models.trading import Order
from src.data.models.strategy_fsm import FSMStrategyConfig


class ValidationResult(BaseModel):
    is_valid: bool
    errors: list[str] = Field(default_factory=list)


class Validator:
    """6.7 — 시스템 전체의 기본 방어선. 정책 위반 여부는 Policy Engine(FROZEN)의 책임이며,
    여기서는 순수 데이터 형식·필수값 검증만 수행한다."""

    def validate_order_params(self, order: Order) -> ValidationResult:
        """수량>0, 가격 형식, tick_size 배수 여부 등 형식 검증만.
        '이 주문이 Risk 한도 내인가'는 검증하지 않는다 — 그건 Risk Engine(FROZEN)."""
        ...

    def validate_strategy_config(self, config: FSMStrategyConfig) -> ValidationResult:
        """9.11 FSM 구조 자체의 무결성 검증 — 모든 state가 최소 1개 이상의 transition을
        가지는지, initial_state가 states 목록에 포함되는지 등."""
        ...
```

## §3.4 Scanner — `src/core/scanner/`

```python
# STATUS: SCAFFOLD-READY
class ScanCriteria(BaseModel):
    min_volume_24h: Decimal | None = None
    min_volatility: Decimal | None = None
    exchanges: list[str] = Field(default_factory=list)


class Scanner:
    """6.8 — 투자전략 자체와 분리. 조건에 맞는 종목을 찾을 뿐 매매 판단은 하지 않는다."""

    async def scan_market(self, criteria: ScanCriteria) -> list[str]:
        """조건을 만족하는 symbol 목록 반환."""
        ...
```

---

## §3.5 Strategy — `src/core/strategy/` ⚠️ FROZEN-PAPER-ONLY (ADR-2026-08-29-E)

```python
# STATUS: FROZEN-PAPER-ONLY — 구현 완료(src/core/strategy/engine.py).
# LIVE 경로는 15.6-D 조건 2(실계정 MFA·이중승인) 충족 전까지 코드 레벨 하드 차단
from abc import ABC, abstractmethod

class Signal(BaseModel):
    """8.1 Signal Engine이 소비하는 구조화된 신호. 9.11 FSM 상태전이 결과로 생성됨."""
    strategy_id: str
    strategy_version: str
    symbol: str
    direction: OrderSide
    confidence: float
    target_position: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    timestamp: datetime


class StrategyEngine(ABC):
    """8.2-A Master Authority 원칙 — 이 클래스는 '의도'만 생성한다.
    실제 주문 여부·수량은 Portfolio+Risk(모두 FROZEN)가 결정하며,
    이 클래스가 직접 Executor를 호출하는 경로는 존재하지 않는다."""

    @abstractmethod
    def evaluate(self, fsm_config: FSMStrategyConfig, market_state: dict) -> Signal | None:
        raise NotImplementedError(
            "FROZEN Zone — 15.6-D 종료조건(Master Authority 구현+회귀테스트, "
            "Human Approval 보안 적용, 자율성 Level 3 Hard Gate 충족) 이후 구현"
        )
```

## §3.6 Portfolio — `src/core/portfolio/` ⚠️ FROZEN-PAPER-ONLY (ADR-2026-08-29-E)

```python
# STATUS: FROZEN-PAPER-ONLY — 구현 완료(src/core/portfolio/engine.py).
class AllocationDecision(BaseModel):
    symbol: str
    strategy_id: str
    approved_quantity: Decimal
    capital_pct: Decimal  # 8.2-B 전략별 자본배분 한도 참조


class PortfolioEngine(ABC):
    """8.2-B 개별 지표 + 8.2-C 포트폴리오 전체 집계를 모두 확인해야 함."""

    @abstractmethod
    def allocate(self, signal: Signal, current_portfolio_state: dict) -> AllocationDecision:
        raise NotImplementedError("FROZEN Zone — 15.6-D 이후 구현")
```

## §3.7 Risk — `src/core/risk/` ⚠️ FROZEN-PAPER-ONLY (Master Authority 핵심, ADR-2026-08-29-E)

```python
# STATUS: FROZEN-PAPER-ONLY — 구현 완료(src/core/risk/engine.py).
class RiskCheckResult(BaseModel):
    approved: bool
    rejection_reason: str | None = None
    checked_rules: list[str]  # 8.2-B 8개 지표 중 어떤 것을 체크했는지 감사 추적용


class RiskEngine(ABC):
    """8.2-A Master Authority의 핵심 구현체 — 이 클래스는 어떤 LLM/Agent의 판단도
    거치지 않고 결정론적 규칙(8.2-B Draft 수치)만으로 동작해야 한다.
    이 클래스는 시스템에서 가장 마지막까지, 가장 신중하게 구현되어야 하는 부분이다."""

    @abstractmethod
    def check(self, allocation: AllocationDecision, account_state: dict) -> RiskCheckResult:
        """8.2-B 8개 지표(Daily Loss, MDD, 레버리지, 집중도, 전략배분, VaR, 상관관계, 거래빈도)
        + 8.2-C 포트폴리오 집계 + 8.1-A 데이터 신뢰도(SSL 후보설계 채택 시 SSL 값)를
        모두 확인 후 승인/거부를 결정한다."""
        raise NotImplementedError(
            "FROZEN Zone — 시스템에서 가장 안전에 민감한 컴포넌트. "
            "15.6-D 종료조건 충족 및 8.6-A-1-1(Watchdog 시뮬레이터), "
            "8.2-D(지연벤치마크) 통과 후 구현 착수"
        )
```

## §3.8 Executor — `src/core/executor/` ⚠️ FROZEN-PAPER-ONLY (ADR-2026-08-29-E)

```python
# STATUS: FROZEN-PAPER-ONLY — 구현 완료(src/core/executor/executor.py,
# LIVE 하드가드: FrozenZoneLiveModeBlockedError).
class Executor(ABC):
    """8.2 원칙 — 전략을 판단하지 않는다. 승인된 AllocationDecision + RiskCheckResult만 받아
    ExchangeAdapter.place_order()를 호출한다."""

    @abstractmethod
    async def execute(
        self,
        allocation: AllocationDecision,
        risk_result: RiskCheckResult,
        adapter: "ExchangeAdapter",
    ) -> Order:
        if not risk_result.approved:
            raise ValueError("Risk 미승인 건은 Executor에 도달해서는 안 됨 — 상위 로직 오류")
        raise NotImplementedError("FROZEN Zone — 15.6-D 이후 구현")
```

---

## §3.9 Zone 경계 요약

```
[ SCAFFOLD — 지금 구현 ]
Loader → Parser → Validator → Scanner
Exchange Adapter (인터페이스 + Mock/Demo 구현체)
Data Models (전체)

                    ↓ ADR-2026-08-29-E (PAPER 한정 개방) ↓

[ FROZEN-PAPER-ONLY — 구현 완료(PAPER만, 2026-09-01) ]
Strategy → Portfolio → Risk → Executor

                    ↓ 15.6-D 조건 2(실계정 MFA·이중승인) 충족 + 별도 ADR ↓

[ 완전 개방 — LIVE 포함 ]
```
