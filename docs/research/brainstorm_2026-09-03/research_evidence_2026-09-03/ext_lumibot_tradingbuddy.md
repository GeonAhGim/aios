# Lumibot & Trading-Buddy — Execution/Multi-Agent 계층 코드 분석 (AIOS 트레이딩 OS 설계 참고자료)

**저장소 A**: `Lumiwealth/lumibot` — shallow clone, `C:/Users/aiaa1/AppData/Local/Temp/claude/.../scratchpad/ext3/lumibot`
**저장소 B**: `heyhaigh/trading-buddy` — shallow clone, `.../scratchpad/ext3/trading-buddy`
**비교 대상**: `research_evidence_2026-09-03/ext_lean.md` (QuantConnect LEAN, C#, IBrokerageModel 기반 심층 분석 기완료)

---

## 0. 메타데이터 / 저장소 건강도 (GitHub API 조회)

| 항목 | Lumibot | Trading-Buddy |
|---|---|---|
| 라이선스 (GitHub 판정) | **GPL-3.0** | NOASSERTION (README 배지는 "MIT" 주장) |
| 생성일 | 2020-09-10 | 2026-01-24 |
| 최근 push | 2026-09-03 (오늘, 활발) | 2026-01-24 (**생성 당일 이후 무변화, 7개월+ 정체**) |
| Star / Fork | 2,035 / 390 | 17 / 4 |
| Contributors | 30 | **1** |
| Open issues | 87 | 0 |
| 커밋 수 | 지속적 (수천+) | **6개** (전체 히스토리) |
| 테스트 | `tests/`, `tests/backtest/`, `tests/backtesting/`, `tests/performance/` 등 다수 디렉터리 | `.spec.ts` 28개 파일(vitest) — 실행/통과 여부는 미검증 |
| 언어 구성 | 순수 Python (3.10+) | **TypeScript가 주력**(153 `.ts` vs 92 `.py`); Python은 `python_risk_manager/`(옵션 리스크 계산기, 4개 파일)에 국한 |

**중요 발견 1 — 라이선스 불일치**: 저장소 루트의 `LICENSE` 파일(673줄)은 전문(全文) **GNU GPL v3.0**이고 GitHub의 자동 라이선스 판정도 `GPL-3.0`이다. 그런데 `setup.py`에는 다음과 같이 명시되어 있다.

```python
# setup.py:44-54, 151-156
setuptools.setup(
    name="lumibot",
    ...
    license="MIT",  # Add license argument
    ...
    classifiers=[
        ...
        "License :: OSI Approved :: MIT License",
```

즉 패키지 메타데이터(PyPI에 노출되는 `license`/classifier)는 **MIT라고 주장**하지만 실제 저장소 라이선스 파일은 **GPL-3.0**이다. 이는 라이선스 담당자의 실수로 보이며, AIOS가 이 코드를 실제로 의존/포함하려면 **PyPI 메타데이터를 신뢰하지 말고 GitHub LICENSE 원문(GPL-3.0)을 기준으로 법무 검토**해야 한다. GPL-3.0은 카피레프트이므로, lumibot을 라이브러리로 단순 `import`하는 것과 코드를 복사/파생하는 것은 의무 수준이 다르다 — 링킹만으로는 일반적으로 GPL 전파 의무가 발생하지 않는다는 해석이 있으나(FSF 자신도 "linking exception 없는 GPL 라이브러리"의 애매함을 인정, LICENSE 끝부분에 LGPL 안내 존재), **AIOS가 상용/폐쇄형 제품이라면 lumibot 소스 재사용은 GPL-3.0 의무(소스 공개, 동일 라이선스 전파)를 촉발할 위험이 있어 코드 차용보다는 "설계 패턴 참고 후 독자 구현"이 안전**하다.

**중요 발견 2 — trading-buddy는 사실상 1인 스냅샷 프로젝트**: 2026-01-24 하루 동안 생성되고 push된 뒤 이후 커밋이 전무하다(오늘 기준 정확히 7개월+ 방치). Contributor 1명, 커밋 6개. "A+ 등급"이라는 평가는 **코드 패턴의 완성도**에 대한 것이지 **프로젝트의 성숙도/신뢰도**에 대한 것이 아님을 분명히 해야 한다. 아래 Part B 분석은 "실전 검증된 라이브러리"가 아니라 "잘 설계된 참고 스니펫 모음"으로 취급한다.

---

# Part A. Lumibot — Execution Layer 심층 분석

## A.1 Broker 추상화: `lumibot/brokers/broker.py`

`Broker`는 `ABC`이며 `lumibot/brokers/broker.py` 한 파일이 **3,476줄**이다. 얼핏 "가벼운 프레임워크"로 알려져 있으나, 실제로는 **주문 동기화(sync_orders/sync_positions), 텔레메트리, 옵션 라이프사이클(assigned/exercised/expired/cash-settled), 시장시간 캘린더, 정리(cleanup) 스레드**까지 포함하는 두꺼운 베이스 클래스다. 다만 하위 브로커가 **반드시 구현해야 하는 추상 메서드는 12개뿐**이다.

```python
# lumibot/brokers/broker.py:893-911 (발췌)
@abstractmethod
def cancel_order(self, order: Order) -> None:
    """Cancel an order at the broker"""
@abstractmethod
def _modify_order(self, order: Order, limit_price=None, stop_price=None):
    ...
@abstractmethod
def _submit_order(self, order: Order) -> Order:
    """Submit an order to the broker"""
```

나머지 9개는 `_get_balances_at_broker`, `get_historical_account_value`, `_get_stream_object`, `_register_stream_events`, `_run_stream`, `_pull_positions`, `_pull_position`, `_parse_broker_order`, `_pull_broker_order`, `_pull_broker_all_orders`다. 즉 **"주문 제출/취소/수정 + 포지션·잔고 조회 + 스트림 연결"이라는 최소 계약**만 강제하고, 주문 상태 전이(new→partial→filled), 포지션 병합, 재시도/캐시 무효화 같은 **복잡한 로직은 전부 base class가 소유**한다. 이는 LEAN의 `IBrokerageModel`(정책 객체를 브로커별로 조합하는 컴포지션 방식)과 달리 **상속 기반 템플릿 메서드 패턴**이며, "얇은 인터페이스 + 두꺼운 베이스 클래스"라는 점에서 LEAN보다 신규 브로커 추가 진입장벽이 낮다.

실제 백엔드 수와 크기(`lumibot/brokers/*.py`):

```
alpaca.py                    2001 lines
bitunix.py                    825 lines
ccxt.py                        866 lines
example_broker.py              246 lines   ← 최소 구현 예시(템플릿)
interactive_brokers.py        1742 lines
interactive_brokers_rest.py   1296 lines
polymarket.py                 1341 lines
projectx.py                   1739 lines
schwab.py                     3399 lines
tradier.py                    2347 lines
tradovate.py                  1476 lines
```

11개 실사용 브로커(주식: Alpaca/IBKR/Tradier/Schwab, 크립토: ccxt/Bitunix, 선물: Tradovate/ProjectX, 예측시장: Polymarket) + 1개 예시(`example_broker.py`, 246줄로 "브로커 하나 추가하는 데 필요한 최소 분량"을 보여줌). LEAN이 `IBrokerageModel` 하나로 신규/체결/마진/설정 4개 정책을 한 번에 번들링하는 것과 비교하면, Lumibot은 **정책 번들링이 없고 단일 추상 클래스 상속뿐**이라 개념적으로는 더 단순하지만, 브로커 파일 자체는 (Schwab 3,399줄처럼) LEAN의 개별 브로커리지 어댑터 못지않게 커질 수 있다 — "얇음"은 인터페이스 계약에 한정되고, 구현 난이도 자체가 낮아지는 것은 아니다.

**AIOS 시사점**
- 12개 추상 메서드로 최소 계약을 강제하고 나머지 전부를 base class가 흡수하는 구조는, AIOS가 신규 브로커 어댑터를 빠르게 붙이고 싶을 때(파일럿/PoC 단계) 참고할 만한 낮은 진입장벽 패턴이다. 다만 LEAN의 `IBrokerageModel`처럼 "신규 주문 검증 정책", "체결 모델", "마진 모델"을 브로커별로 명시적으로 교체 가능한 정책 객체로 분리하지 않기 때문에, AIOS가 기관형 다중 계좌 정책(PDT, 계좌 유형별 마진)을 도입하려면 Lumibot 패턴을 그대로 쓰기보다 **LEAN의 정책 객체 분리 + Lumibot의 얇은 추상 메서드 계약**을 혼합하는 것이 유리하다.

---

## A.2 Backtest/Live Parity — `lumibot/backtesting/backtesting_broker.py`

`BacktestingBroker`는 **`Broker`를 그대로 상속**한다.

```python
# lumibot/backtesting/backtesting_broker.py:134
class BacktestingBroker(Broker):
```

그리고 `Strategy.backtest()` / `run_backtest()`가 백테스트용 브로커를 생성해 **동일한 Strategy 생성자**에 주입하는 지점을 직접 확인했다:

```python
# lumibot/strategies/_strategy.py:3468-3488 (발췌)
if not use_other_option_source:
    backtesting_broker = BacktestingBroker(data_source)
else:
    ...
    backtesting_broker = BacktestingBroker(data_source, options_source)

strategy = self(
    backtesting_broker,
    minutes_before_closing=minutes_before_closing,
    ...
)
```

라이브 트레이딩 경로도 동일 `Strategy.__init__(broker, ...)` 시그니처를 사용하며 `broker` 인자만 `Alpaca(...)`/`InteractiveBrokers(...)` 등 실 브로커 인스턴스로 바뀐다. 즉 **전략 코드, `Broker` 인터페이스, 주문/포지션 엔티티가 백테스트와 라이브에서 100% 동일**하고 오직 `Broker` 구현체만 교체되는 구조 — LEAN/QuantDinger/Freqtrade가 공통으로 추구하는 "shared-domain-code" 원칙을 Lumibot도 정확히 따른다. `BacktestingBroker`가 5,024줄(`backtesting_broker.py`)로 브로커 파일 중 가장 크다는 점은, "backtest/live parity"를 유지하기 위해 실제 브로커의 체결/부분체결/옵션 라이프사이클/콤보 주문 로직을 **시뮬레이션 레이어에서 전부 재현**해야 하는 비용을 보여준다(이는 LEAN이 `BacktestingBrokerage` + 각종 `FillModel`로 나눠서 처리하는 것과 대비해, Lumibot은 단일 거대 클래스에 응집시킨 차이).

**AIOS 시사점**
- "전략 클래스는 절대 변경되지 않고 오직 Broker 구현체만 교체"라는 원칙은 AIOS Execution Plane의 핵심 불변식으로 그대로 채택할 가치가 있다 — backtest/paper/live 3단계 승격에서 전략 코드 diff가 0이어야 한다는 검증 기준으로 사용 가능.
- 다만 5,000줄짜리 단일 `BacktestingBroker` 클래스는 유지보수 관점에서 경고 신호다. AIOS는 이 책임(체결 시뮬레이션, 콤보 분해, 옵션 라이프사이클, TIF 만료)을 LEAN처럼 별도 정책 객체(FillModel/ComboResolver/OptionLifecycleModel)로 쪼개어 동일한 parity 원칙을 더 낮은 결합도로 달성해야 한다.

---

## A.3 Order/Position 상태 모델 — `lumibot/entities/order.py` (1,582 lines)

Order 클래스 내부에 5개의 `StrEnum`이 정의되어 있다:

```python
# lumibot/entities/order.py:169-211 (발췌)
class OrderClass(StrEnum):
    SIMPLE = "simple"
    BRACKET = "bracket"
    OCO = "oco"
    OTO = "oto"
    MULTILEG = "multileg"

class OrderType(StrEnum):
    MARKET = "market"; LIMIT = "limit"; STOP = "stop"; STOP_LIMIT = "stop_limit"
    TRAIL = "trailing_stop"; SMART_LIMIT = "smart_limit"; UNKNOWN = "unknown"

class OrderStatus(StrEnum):
    UNPROCESSED = "unprocessed"; SUBMITTED = "submitted"; OPEN = "open"; NEW = "new"
    CANCELLING = "cancelling"; CANCELED = "canceled"; FILLED = "fill"
    PARTIALLY_FILLED = "partial_fill"; CASH_SETTLED = "cash_settled"
    ASSIGNED = "assigned"; EXERCISED = "exercised"; ERROR = "error"
    EXPIRED = "expired"; UNKNOWN = "unknown"
```

이는 LEAN의 `Order`(abstract class) + `OrderTicket`(핸들, 이벤트 누적) + `OrderEvent`(불변 이벤트) 3계층 분리와 달리 **단일 `Order` 클래스가 상태·이벤트·콤보 트리를 전부 겸함** — 확실히 더 단순하다. 그러나 실제로 잃는 기능은 예상보다 적다:

- **Combo order**: `OrderClass.BRACKET/OCO/OTO/MULTILEG` 4종을 지원하며, `Order.__init__`이 `child_orders`를 직접 생성한다(order.py:926-1056, OCO/BRACKET/OTO 각각 자식 limit/stop 주문을 즉석 생성). `BacktestingBroker._flatten_order`(backtesting_broker.py:779-903)가 OCO는 "둘 중 하나 체결 시 나머지 취소", BRACKET/OTO는 "부모 체결 후 자식 활성화"를 각각 처리한다.
- **부분체결(Partial fill)**: `Broker._process_partially_filled_order`가 처리한다.

```python
# lumibot/brokers/broker.py:1949-1968
def _process_partially_filled_order(self, order, price, quantity):
    self._new_orders.remove(order.identifier, key="identifier")
    order.add_transaction(price, quantity)
    order.status = self.PARTIALLY_FILLED_ORDER
    order.set_partially_filled()
    if order not in self._partially_filled_orders:
        self._partially_filled_orders.append(order)
    position = self.get_tracked_position(order.strategy, order.asset)
    if position is None:
        position = order.to_position(quantity)
    else:
        position.add_order(order)
    ...
```

  LEAN의 `OrderTicket`처럼 매 이벤트마다 불변 `FillState`를 새로 만드는 방식은 아니고 `order.add_transaction()`으로 누적 mutate하는 단순 방식 — 스레드 안전성 보장 수준은 LEAN보다 약하다(락 없이 리스트 append/remove).
- **TIF**: `Order.time_in_force`는 단순 문자열(기본값 `"day"`)이다. 그런데 `BacktestingBroker`에 **IOC/FOK를 문자열로 명시 지원하는 실제 로직**이 있다 — 이는 LEAN 코어(`ext_lean.md` §9.1)가 GTC/Day/GTD만 지원하고 **IOC/FOK가 아예 없다**고 확인된 것과 대비되는, Lumibot이 LEAN 코어보다 명확히 앞서는 지점이다:

```python
# lumibot/backtesting/backtesting_broker.py:3598-3616 (발췌)
def _cancel_unfilled_immediate_time_in_force(self, order, reason, strategy=None) -> bool:
    """Cancel IOC/FOK orders that reached a current quote/bar but did not execute."""
    tif = self._normalize_time_in_force(order)
    if tif not in {"ioc", "immediate_or_cancel", "fok", "fill_or_kill"}:
        return False
    ...
    self.cancel_order(order)
    return True
```

**정말로 잃는 것**: LEAN의 `OrderRequest`(자신의 Response/Status를 보관하는 능동 객체)나 `OrderTicket`의 "존재하지 않는 주문 ID에도 항상 가짜 티켓을 반환"하는 API 계약, `GroupOrderManager`의 "전량 도착 후에만 원자적 제출" 검증 같은 **엔터프라이즈급 방어 로직은 Lumibot에 없다**. 콤보 주문도 부모/자식을 즉시 생성해버리는 낙관적 방식이라, "레그 중 하나가 브로커 거부 시 전체 롤백" 같은 원자성 보장이 LEAN만큼 명시적이지 않다(추적 결과 `InvalidateOrders`에 해당하는 전체 무효화 루틴은 발견되지 않음, 개별 레그 단위 에러 처리에 가까움).

**AIOS 시사점**
- Order 상태·이벤트·콤보를 단일 클래스에 응집시키는 것은 소규모/중간 규모 전략에는 충분히 실용적이나, AIOS가 목표로 하는 "주문 무결성 감사"에는 LEAN의 3계층 분리(불변 Order + 능동 Request + 핸들 Ticket)가 더 안전하다 — **Order 모델은 LEAN 패턴을 채택하되, TIF는 Lumibot처럼 IOC/FOK를 1급 문자열로 먼저 지원**하는 것이 실용적 절충안이다.
- 콤보 주문을 부모 생성 시점에 자식까지 한 번에 만들어버리는 Lumibot 방식은 코드는 단순하지만 "레그 일부만 거부됐을 때의 원자적 롤백"이 약하다 — AIOS는 이 부분에서 LEAN의 `GroupOrderManager` "전량 도착 후 제출" 원칙을 반드시 채택해야 한다.

---

## A.4 Data Feed 추상화 & Look-ahead Bias 방지

`lumibot/data_sources/data_source.py`의 `DataSource(ABC)`도 Broker와 동일하게 **얇은 인터페이스**(추상 메서드 3개: `get_chains`, `get_historical_prices`, `get_last_price`)에 `get_bars`, `get_datetime_range`, `get_round_minute` 등 다수의 공통 유틸리티가 base class에 구현되어 있다.

Look-ahead 방지는 산발적이지만 실제 코드에 여러 지점 존재하고, 회귀 테스트로 고정되어 있다는 언급도 있다:

```python
# lumibot/data_sources/yahoo_data.py:400-406
# Daily bars are stamped at the session close. Leaving the timeshift unset for daily
# requests ensures we only reference the most recent fully closed bar (no lookahead).
# Intraday paths still step back one interval to avoid peeking ahead.
if isinstance(timestep, str) and 'day' in timestep.lower():
    timeshift_delta = None
else:
    timeshift_delta = timedelta(days=-1)
```

```python
# lumibot/backtesting/backtesting_broker.py:2475 (주석)
# completed bar. See tests/*_lookahead for regression coverage.
```

`databento_backtesting_pandas.py:662`, `databento_backtesting_polars.py:871`에는 `"CRITICAL: NEGATIVE TIMESHIFT ARITHMETIC FOR LOOKAHEAD"`라는 강조 주석과 함께 명시적 처리가 있고, `thetadata_backtesting_pandas.py:3254`는 "Drop any future bars to avoid lookahead when requesting intraday data" 로직을 갖는다. 즉 **look-ahead 방지가 프레임워크 레벨의 통일된 정책(예: LEAN의 `Slice`/`Time` 캡슐화처럼 구조적으로 원천 차단)이 아니라, 데이터소스별로 개별 구현된 방어 코드의 집합**이다 — 신규 데이터소스를 추가하는 개발자가 이 관례를 놓치면 look-ahead bias가 재도입될 위험이 구조적으로 존재한다(LEAN은 `Slice` 객체 자체가 "현재 시점까지의 데이터만 담는" 캡슐이라 구조적으로 막힘).

**AIOS 시사점**
- Lumibot의 look-ahead 방지는 "개발자 각자가 주석과 회귀 테스트로 지킨다"는 관습 기반 방어라 신뢰도가 LEAN보다 낮다. AIOS는 이 갭을 인지하고, 신규 데이터소스 어댑터 추가 시 **look-ahead 회귀 테스트를 프레임워크 레벨에서 강제**(예: 데이터소스 계약 테스트 스위트에 "미래 데이터 요청 시 예외" 케이스를 필수 포함)해야 한다.
- `DataSource` 역시 3개 추상 메서드로 얇게 설계된 점은 AIOS가 자체 데이터 프로바이더(사내 틱 DB 등)를 빠르게 붙이는 데 참고할 만하다.

---

## A.5 Fee/Slippage 모델 — LEAN 대비 명확한 단순화 지점

```python
# lumibot/entities/trading_slippage.py (전체 28줄)
class TradingSlippage:
    def __init__(self, amount=0.0):
        ...
```

`TradingSlippage`는 전략이 설정하는 **고정 금액/비율**일 뿐이며, LEAN의 `VolumeShareSlippageModel`(거래량 대비 슬리피지 스케일링)처럼 시장 미시구조를 반영하지 않는다. 실제 적용 지점도 단순하다:

```python
# lumibot/backtesting/backtesting_broker.py:4548-4557 (발췌)
slippage_amount = smart_limit.get_slippage_amount()
if smart_limit.slippage is None:
    slippage_amount = self._get_strategy_slippage_amount(strategy, order)
# Backtesting model: fill at mid +/- slippage (inside the spread) whenever bid/ask are available.
fill_price = expected_fill_price(mid, slippage_amount, side)
```

"mid ± 고정 slippage"라는 단일 모델이며, 주문 크기·유동성·변동성에 연동되는 슬리피지는 없다. `TradingFee`(51줄)도 유사하게 단순한 고정 수수료 구조체다. 이는 Freqtrade/LEAN 대비 명확한 단순화 지점으로, **AIOS가 실제로 필요로 할 가능성이 높은 "주문 크기 대비 시장충격" 모델링은 Lumibot에서 가져올 것이 없다** — 이 부분은 LEAN의 `ISlippageModel`/`IFillModel` 정책 객체 패턴을 참고해야 한다.

---

## A.6 패키징 / 의존성 footprint

`setup.py`의 `install_requires`는 70개 이상의 하드 의존성을 나열한다(발췌):

```
polygon-api-client, alpaca-py, ibapi==9.81.1.post1, yfinance, pandas, polars,
ccxt, schwab-py, py-clob-client-v2 (Polymarket), databento, duckdb,
google-adk[extensions]>=2.1.0, google-genai, litellm, mcp>=1.26.0, openai,
Flask, boto3, sqlalchemy, psycopg2-binary, ...
```

두 가지가 놀랍다: (1) 브로커별 SDK(alpaca-py, ibapi, ccxt, schwab-py, py-clob-client-v2)가 **extras가 아니라 전부 필수 의존성**이라 최소 설치조차 무겁다(ThetaData만 유일하게 `extras_require`로 분리됨). (2) `google-adk`, `google-genai`, `litellm`, `mcp`, `openai`가 포함된 것은 lumibot이 최근 **AI 에이전트 컴포넌트**(`lumibot/components/agents/skills/*/SKILL.md` 패키지 데이터로 확인)를 프레임워크에 흡수했다는 뜻 — "가벼운 순수 Python 실행 프레임워크"라는 평판과 달리, 실제 최신 버전(4.5.89)의 의존성 그래프는 **크립토 거래소, 옵션 그릭스, LLM 에이전트 프레임워크를 모두 아우르는 매우 넓은(broad) footprint**로 성장했다.

**결론**: "AIOS가 Lumibot을 pip 의존성으로 직접 설치"하는 것은 비현실적이다(브로커별 SDK 강제 설치, GPL-3.0 라이선스, LLM 프레임워크 중복). 반면 **LEAN(.NET 런타임 필요, C#, 프로세스 경계 넘는 IPC 필요)보다는 압도적으로 이식이 쉽다** — 순수 Python이므로 `Broker`/`DataSource`의 "얇은 추상 메서드 + 두꺼운 base class" 설계, Order의 5-Enum 상태 모델, IOC/FOK 문자열 처리 로직 등은 **코드를 그대로 복사하지 않고 설계를 재구현(reimplementation)** 하는 방식으로 AIOS에 이식하는 것이 법적(GPL 회피)·기술적(불필요 의존성 제거) 양면에서 타당하다.

---

## A. 종합 AIOS 시사점 (Execution Plane)

1. **Lumibot을 LEAN보다 우선하는 1차 참조로 채택할 근거는 충분하다** — AIOS가 Python 네이티브인 이상, .NET 런타임과 C# 마샬링 비용이 드는 LEAN을 실행 엔진 참조로 직접 이식하는 것은 항상 2차 번역 비용을 수반한다. Lumibot은 **"Broker/DataSource를 얇은 ABC로 두고 나머지 로직을 base class가 흡수"하는 패턴, "Strategy는 그대로 두고 Broker 구현체만 교체"하는 backtest/live parity 원칙, IOC/FOK를 문자열로 즉시 지원하는 실용주의**를 코드 그대로 참고할 수 있는 동일 언어 레퍼런스다.
2. 그러나 **"패턴은 Lumibot, 방어 로직은 LEAN"이 정답이다.** Order 상태 캡슐화(불변 필드 + internal setter), GroupOrderManager의 전량 도착 후 원자적 제출, ISlippageModel/IFillModel의 정책 객체 분리, IBrokerageModel의 정책 번들링 — 이 네 가지는 Lumibot에 없거나 약하며, AIOS가 기관형 신뢰성을 확보하려면 LEAN에서 가져와야 한다.
3. **법적으로 Lumibot 코드를 직접 복사하는 것은 금지 수준으로 취급**해야 한다(GPL-3.0, setup.py의 MIT 표기는 오기로 판단). "설계를 읽고 독자적으로 재작성"하는 clean-room 방식이 유일하게 안전한 경로다.
4. Lumibot의 look-ahead 방지가 관습 기반(주석 + 개별 회귀 테스트)이라는 점은 AIOS 데이터 소스 어댑터 계약 테스트에 "미래 데이터 접근 금지"를 구조적으로 강제하는 계기로 삼아야 한다.
5. 의존성 footprint 분석 결과, Lumibot 자체를 pip 의존성으로 끌어오는 것은 비권장 — **읽고 재구현하되 의존하지 않는다(reference, not dependency)**는 원칙을 AIOS 아키텍처 결정 기록(ADR)에 명시할 것을 권고한다.

---

# Part B. Trading-Buddy — Multi-Agent / Data-Failover / Audit 패턴 분석

앞서 밝혔듯 이 저장소는 **2026-01-24 단 하루 동안 만들어진 1인 프로젝트**(커밋 6개, contributor 1명, 이후 7개월+ 무변화)이며, 코드의 절반 이상이 Python이 아닌 TypeScript다(`src/`, `trading-agent/`). 아래는 "실전 검증된 프레임워크"가 아니라 **잘 설계된 패턴 스니펫**으로서의 분석이다.

## B.1 Multi-Agent 아키텍처

`trading-agent/src/orchestration/UnifiedOrchestrator.ts`가 별도 **자식 프로세스(child_process.spawn)**로 4개 에이전트를 기동·감시한다:

```typescript
// trading-agent/src/orchestration/UnifiedOrchestrator.ts:138-179 (발췌, id/name/scriptPath만)
{ id: 'main_trading',  name: 'Main Trading Agent',        scriptPath: 'src/cli/runAgent.ts' }
{ id: 'portfolio',     name: 'Portfolio Analysis Agent',  scriptPath: 'src/cli/portfolioAgent.ts' }
{ id: 'options',       name: 'Options Trading Agent',     scriptPath: 'src/cli/simpleOptionsAgent.ts' }
{ id: 'strategy_prep', name: 'Strategy Preparation Agent',scriptPath: 'src/cli/strategyPreparationAgent.ts' }
```

각 에이전트 항목은 `mode`(market_hours/off_hours/always), `priority`, `marketRegimes`(trending/ranging/volatile/low_vol별 우선순위), 자체 `circuitBreaker`(연속 실패 시 backoff), `resourceUsage`/`performance` 메트릭을 갖는다 — **시장 레짐에 따라 에이전트 우선순위를 동적으로 재배치**하는 오케스트레이션 설계다.

에이전트 간 통신은 프로세스 간 공유 `AgentMessageBus`(EventEmitter 기반, JSON 파일로도 영속화)를 통한 **pub/sub**이다:

```typescript
// trading-agent/src/shared/AgentMessageBus.ts:14-31 (발췌)
interface AgentMessage {
  id: string; from: string; to?: string; // undefined = broadcast
  type: 'market_alert' | 'strategy_update' | 'risk_warning' | 'opportunity' | 'coordination';
  priority: 'low' | 'medium' | 'high' | 'critical';
  data: any; timestamp: string; expires?: string;
}
interface AgentStatus {
  agent_id: string; status: 'active' | 'standby' | 'offline';
  last_heartbeat: string; market_mode: 'trading' | 'monitoring' | 'preparation';
  capabilities: string[];
}
```

타입 있는 메시지 카테고리(시장 경보/전략 갱신/리스크 경고/기회/조정), 브로드캐스트 vs 유니캐스트(`to` 필드 유무), 하트비트 기반 에이전트 상태 관리(active/standby/offline)까지 갖춘 것은 "장난감 데모"치고는 상당히 완성도 있는 설계다. 다만 실제 프로세스 간 통신이 파일시스템 JSON 폴링에 의존하는 부분이 있어(EventEmitter는 단일 프로세스 내에서만 작동, 프로세스 간에는 `dataDir` 파일 공유로 추정) 진짜 분산 메시지 버스는 아니다 — Kafka/Redis 같은 실제 브로커 없이 "in-process EventEmitter + 파일 폴백"으로 멀티프로세스를 흉내내는 구조다.

## B.2 데이터 페일오버 패턴 — AIOS DataDistrust와의 대응

`src/data/ProviderRouter.ts`가 WebSocket 우선 + REST 폴백 + 다중 프로바이더 합의(consensus) 3단 구조를 구현한다:

```typescript
// src/data/ProviderRouter.ts:162-193 (발췌)
private async fetchQuotesFromProviders(symbol: string): Promise<NormalizedQuote[]> {
  const quotes: NormalizedQuote[] = [];
  if (this.wsConnection.connected) {
    const wsQuote = this.getWSQuote(symbol);
    if (wsQuote && (now - wsQuote.timestamp) < this.cfg.freshness.quotesMs) quotes.push(wsQuote.quote);
  }
  const providers = this.providerRegistry.getHealthyProviders();
  for (const provider of providers) {
    try {
      const quote = await this.providerRegistry.getQuoteAdapter(provider)?.getQuote(symbol);
      if (quote && (now - quote.ts_provider) < this.cfg.freshness.quotesMs) quotes.push(quote);
    } catch (error) { metrics.providerError(provider); }
  }
  return quotes;
}
```

합의 로직(`src/data/consensus.ts`)은 **정족수(quorum) 기반 가격 합의**다 — anchor 프로바이더 대비 bps 이내로 일치하는 프로바이더 수가 `min_quorum` 이상이면 평균값 채택, 미달이면 단일 소스로 폴백하되 **명시적으로 `stale: true`** 표시한다:

```typescript
// src/data/consensus.ts:12-34 (발췌)
export function priceConsensus(quotes: NormalizedQuote[], cfg: ConsensusConfig): ConsensusResult<number> {
  const fresh = quotes.filter(q => q.mid != null && q.spread_bps != null);
  if (fresh.length === 0) return { value: null, providersUsed: [], quorum: 0, threshold_bps: cfg.floor_bps, stale: true };
  const anchor = fresh[0];
  const thr = dynamicThresholdBps(anchor.spread_bps, cfg);
  const agree = [[anchor.provider, anchor.mid!]];
  for (let i=1; i<fresh.length; i++) { if (bps(anchor.mid!, fresh[i].mid!) <= thr) agree.push([fresh[i].provider, fresh[i].mid!]); }
  if (agree.length >= cfg.min_quorum) return { value: avg(agree), providersUsed: agree.map(a=>a[0]), quorum: agree.length, threshold_bps: thr, stale: false };
  return { value: anchor.mid!, providersUsed: [anchor.provider], quorum: agree.length, threshold_bps: thr, stale: agree.length === 1 };
}
```

여기에 `calculateConfidence()`가 quorum/threshold를 기반으로 `high/medium/low` 신뢰도 등급을 산출하고, `ProviderRouter.haltEntriesIfStale(symbol)`이 "신선한 데이터가 하나도 없으면 신규 진입 자체를 halt"하는 명시적 게이트를 제공한다. 이는 **AIOS의 DataDistrust 개념과 구조적으로 거의 동일**하다 — (a) 다중 소스 교차검증, (b) 정족수 미달 시 신뢰도 강등(단순 폴백이 아니라 "낮은 신뢰도" 라벨링), (c) 진입 자체를 막는 명시적 회로차단기(halt). 다만 `PerHostCircuitBreaker`(호스트별 half-open 상태 포함 표준 회로차단기, `src/data/circuitBreaker.ts:1-53`)는 프로바이더 "가용성"만 다루고, consensus는 "가격 신뢰도"만 다루어 **두 축이 별개 모듈로 느슨하게만 연결**되어 있다 — 완전히 통합된 단일 DataDistrust 상태머신은 아니다.

## B.3 감사(Audit) 패턴 — 해시체인 기반 append-only 로그

두 개의 구현이 존재한다. `src/compliance/audit.ts`(7줄, 스텁 수준)와 실제로 쓰이는 `trading-agent/src/audit/auditLog.ts`(282줄)다. 후자가 실질적 구현이다:

```typescript
// trading-agent/src/audit/auditLog.ts:5-18
export type AuditEventType =
  | 'AGENT_START' | 'AGENT_STOP' | 'EMERGENCY_STOP' | 'DECISION_MADE'
  | 'ORDER_SUBMITTED' | 'ORDER_FILLED' | 'ORDER_CANCELLED' | 'ORDER_REJECTED'
  | 'POSITION_ENTERED' | 'POSITION_EXITED' | 'RISK_LIMIT_BREACHED'
  | 'CONFIG_CHANGED' | 'MARKET_HOURS_CHANGE';

export interface AuditEvent {
  id: string; timestamp: number; eventType: AuditEventType; actor: string;
  traceId?: string; metadata: Record<string, any>;
  prevHash: string; // SHA-256 hash of previous event (blockchain-style)
  hash: string;
}
```

```typescript
// trading-agent/src/audit/auditLog.ts:37-46 (발췌)
const DEFAULT_CONFIG: AuditLogConfig = {
  logDir: './audit_logs',
  maxFileSizeMb: 100,
  retentionDays: 2555, // ~7 years (regulatory requirement)
};
export class AuditLog {
  private lastHash: string = '0000000000000000'; // Genesis hash
  ...
```

각 이벤트가 `prevHash`로 직전 이벤트의 SHA-256을 참조하는 **해시체인(append-only, tamper-evident log)** 구조이며, `verifyChain()`(간이 버전은 `src/compliance/audit.ts:7`에도 존재)으로 전체 체인의 무결성을 검증할 수 있다. 로그는 JSONL 형식으로 일자별 파일 로테이션(`audit_YYYY-MM-DD.jsonl`)되고, 보존 기간이 **7년(2555일)으로 규제 요구사항을 명시적으로 주석에 남긴 점**이 눈에 띈다. 이벤트 타입이 주문 라이프사이클(SUBMITTED/FILLED/CANCELLED/REJECTED)뿐 아니라 에이전트 라이프사이클(START/STOP/EMERGENCY_STOP)과 리스크 이벤트(RISK_LIMIT_BREACHED), 설정 변경(CONFIG_CHANGED)까지 포괄한다는 점에서, LEAN의 "OrderRequest가 자신의 Response/Status를 보관"하는 방식보다 **운영 감사 관점에서는 더 포괄적**이다. 다만 파일시스템 기반(외부 WORM 스토리지·서명 없음)이라 실제 규제 감사 요구(비가역적 저장, 제3자 타임스탬프)를 완전히 충족하지는 못한다 — "패턴은 맞지만 신뢰 근거(root of trust)가 로컬 파일뿐"이라는 한계가 있다.

## B. 종합 AIOS 시사점 (Execution Plane)

1. **정족수 기반 가격 합의 + 명시적 stale/confidence 등급 + halt 게이트**는 AIOS DataDistrust 설계에 거의 그대로 이식 가능한 참조 구현이다. 다만 이 저장소 자체를 의존성으로 쓰기엔 성숙도가 전혀 없으므로(1인, 6커밋, 7개월 방치), **코드를 참고해 AIOS 자체 모듈로 clean-room 재구현**해야 한다.
2. 해시체인 audit log(prevHash/hash, genesis hash, 7년 보존 주석)는 AIOS의 감사 로그 요구사항(불변성, 체인 검증)에 바로 적용 가능한 최소 설계다. 단, "로컬 JSONL 파일"이 신뢰 근거의 전부라는 한계를 AIOS는 반드시 넘어서야 한다 — WORM 스토리지나 외부 타임스탬프 앵커링(예: 주기적으로 해시를 외부 로그/블록체인에 anchor)을 추가해야 실제 규제 대응력을 갖는다.
3. 4-에이전트 오케스트레이션(시장 레짐별 우선순위, 프로세스 단위 격리, 개별 circuit breaker)은 "에이전트를 프로세스로 격리하고 메시지 버스로만 통신"하는 아키텍처가 장애 격리에 유리하다는 정성적 근거는 되지만, 실제 분산 메시지 브로커가 아닌 EventEmitter+파일 폴백이라는 점에서 **AIOS는 이 패턴의 "설계 의도"만 채택하고 구현은 실제 메시지 큐(Redis Streams/NATS 등)로 교체**해야 한다.
4. **전반적 권고**: trading-buddy는 라이브러리/의존성으로 사용하기에는 부적합(단일 커밋 히스토리, 테스트 통과 여부 미검증, 프로덕션 하드닝 없음)하지만, "멀티 에이전트+데이터 페일오버+감사"라는 3개 영역 모두에서 AIOS가 필요로 하는 개념을 **압축된 형태로 미리 스케치해놓은 참고 자료**로서는 A+ 등급이 타당하다.

---

## 부록: 조사에 사용한 핵심 파일 목록

```
[Lumibot]
lumibot/brokers/broker.py (3476 lines, 핵심 — Broker ABC, 12개 abstractmethod)
lumibot/brokers/{alpaca,schwab,tradier,interactive_brokers,ccxt,tradovate,projectx,polymarket,bitunix,example_broker}.py
lumibot/backtesting/backtesting_broker.py (5024 lines, 핵심 — BacktestingBroker(Broker))
lumibot/data_sources/data_source.py, yahoo_data.py
lumibot/backtesting/{databento_backtesting_pandas,databento_backtesting_polars,thetadata_backtesting_pandas}.py (lookahead 처리)
lumibot/entities/order.py (1582 lines, OrderClass/OrderType/OrderStatus)
lumibot/entities/{position,trading_fee,trading_slippage,smart_limit}.py
lumibot/strategies/_strategy.py (run_backtest → BacktestingBroker 생성 지점)
setup.py (라이선스 메타데이터 불일치 확인), LICENSE (GPL-3.0 전문)

[Trading-Buddy]
trading-agent/src/orchestration/UnifiedOrchestrator.ts (4-agent 프로세스 오케스트레이션)
trading-agent/src/shared/AgentMessageBus.ts (pub/sub 메시지 버스)
trading-agent/src/audit/auditLog.ts (282 lines, 해시체인 audit log)
src/compliance/audit.ts (7 lines, 스텁)
src/data/ProviderRouter.ts, consensus.ts, circuitBreaker.ts (데이터 페일오버 3단 구조)
python_risk_manager/options_risk_manager.py (유일한 실질 Python 컴포넌트)
README.md, LICENSE (MIT 주장, GitHub는 NOASSERTION)
```

**GitHub API 조회 결과(2026-09-03 기준)**: `gh api repos/Lumiwealth/lumibot` (stars 2035, forks 390, contributors 30, license GPL-3.0, pushed 오늘), `gh api repos/heyhaigh/trading-buddy` (stars 17, forks 4, contributors 1, commits 6, created·pushed 모두 2026-01-24).
