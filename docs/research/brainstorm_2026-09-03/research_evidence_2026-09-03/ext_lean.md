# QuantConnect LEAN — Execution Sem계층 코드 분석 (AIOS 트레이딩 OS 설계 참고자료)

**저장소**: `C:/Users/aiaa1/AppData/Local/Temp/claude/.../scratchpad/ext/Lean` (sparse checkout, 1057+ files)
**라이선스**: Apache License, Version 2.0 (`LICENSE` 파일 확인 완료 — 상업적 사용/수정/재배포 자유, 특허 조항 포함, "AS IS" 무보증)
**최신 커밋**: `f24fc0d3df03d6bdbe0e6fc7b8522445f1d900d2` — "Fix pandas conversion of dynamic data with heterogeneous properties (#9769)", 2026-09-01 (Tue) 18:33:13 -0400
**분석 범위**: Order/Execution 파이프라인 중심 (Order model, TransactionHandler, Brokerage abstraction, Fee/Fill/Slippage, Risk/Execution framework, RealTime/Scheduling, Results/Statistics, Setup/Reconciliation)

---

## 1. Order 모델과 라이프사이클

### 1.1 `Order` (Common/Orders/Order.cs)

`Order`는 abstract class이며, 모든 setter가 `internal`로 캡슐화되어 있어 알고리즘 코드나 외부에서 직접 변경할 수 없다. 상태 변경은 오직 엔진 내부(TransactionHandler 등)의 메서드를 통해서만 이루어진다.

```csharp
// Common/Orders/Order.cs:41-56
[JsonProperty(PropertyName = "id")]
public int Id
{
    get => _id;
    internal set { ... }
}
```

- `Quantity`, `Price`는 `Normalize()`를 거쳐 저장된다(정밀도 정규화).
- `Status`만 `public set`이며 나머지 필드는 `internal set` — order는 사실상 "구조체에 가까운 mutable record"로, 소유권은 엔진(TransactionHandler)에 있다.
- `Direction`은 `Quantity`의 부호로부터 계산되는 **파생 프로퍼티**(저장되지 않음): Quantity>0 → Buy, <0 → Sell, ==0 → Hold.
- `CreateOrder(SubmitOrderRequest)` (Order.cs:413-481) 팩토리 메서드가 `OrderType`별로 구체 클래스(`MarketOrder`, `LimitOrder`, `StopMarketOrder`, `StopLimitOrder`, `TrailingStopOrder`, `LimitIfTouchedOrder`, `MarketOnOpenOrder`, `MarketOnCloseOrder`, `OptionExerciseOrder`, 3종 Combo order)를 생성하고, 생성 직후 `order.Status = OrderStatus.New; order.Id = orderId;`로 초기화한다.
- `ApplyUpdateOrderRequest(UpdateOrderRequest)` (Order.cs:347-361)이 업데이트 요청을 order 객체에 반영하는 유일한 통로이며, `Quantity`/`Tag`만 base class에서 처리하고 파생 클래스(LimitOrder 등)가 override하여 가격 필드를 추가 반영한다.
- `Clone()`/`CopyTo()`로 깊은 복사를 지원 — margin 체크 시 "가상의 업데이트된 order"를 만들어 사전 검증하는 데 사용된다(`HandleUpdateOrderRequest`에서 `order.Clone()` 후 `ApplyUpdateOrderRequest` 호출).

### 1.2 `OrderStatus` (Common/Orders/OrderTypes.cs:138-184)

```csharp
public enum OrderStatus
{
    New = 0, Submitted = 1, PartiallyFilled = 2, Filled = 3,
    Canceled = 5, None = 6, Invalid = 7, CancelPending = 8, UpdateSubmitted = 9
}
```

주의: 값 `4`가 비어 있다(과거 `Held` 상태가 제거된 흔적으로 추정, 하위호환을 위해 번호 재사용 안 함). `OrderExtensions.cs`(20-56)가 상태 분류 헬퍼를 제공한다:

```csharp
// Common/Orders/OrderExtensions.cs:30-56
public static bool IsClosed(this OrderStatus status)
    => status == OrderStatus.Filled || status == OrderStatus.Canceled || status == OrderStatus.Invalid;
public static bool IsOpen(this OrderStatus status) => !status.IsClosed();
public static bool IsFill(this OrderStatus status)
    => status == OrderStatus.Filled || status == OrderStatus.PartiallyFilled;
```

`Invalid`는 "closed" 취급 — 즉 브로커에 도달하기 전 거부된 주문도 "종료" 상태로 간주되어 재시도 불가. `CancelPending`은 "open"으로 분류되어 취소 확정 전까지는 여전히 활성 주문으로 취급된다(마진 계산의 `GetProjectedHoldings`에 영향).

### 1.3 `OrderRequest` / `SubmitOrderRequest` / `UpdateOrderRequest` / `CancelOrderRequest`

세 요청 타입 모두 `OrderRequest` abstract base(Common/Orders/OrderRequest.cs)를 상속하며, 요청 자체가 **자신의 처리 결과(Response/Status)를 보관하는 능동적 객체**다:

```csharp
// Common/Orders/OrderRequest.cs:89-104
public void SetResponse(OrderResponse response, OrderRequestStatus status = OrderRequestStatus.Error)
{
    if (response == null) throw new ArgumentNullException(...);
    Status = response.IsError ? OrderRequestStatus.Error : status;
    Response = response;
}
```

`UpdateOrderRequest`(UpdateOrderRequest.cs)는 모든 필드가 `decimal?`로 nullable — "지정된 필드만 변경"하는 partial-update 패턴이다. `IsAllowedForClosedOrder()`(91-94)는 "닫힌 주문에는 Tag 외의 어떤 필드도 변경 불가"를 강제한다.

### 1.4 `OrderTicket` (Common/Orders/OrderTicket.cs)

알고리즘이 주문을 참조하는 유일한 핸들. 내부적으로 `SubmitOrderRequest`(불변, readonly), `List<UpdateOrderRequest>`, `CancelOrderRequest`(1회성, `TrySetCancelRequest`로 CAS 방식 설정), `List<OrderEvent>`를 스레드-세이프하게 누적한다.

- **평균 체결가 계산**은 이벤트가 들어올 때마다 증분 재계산: `_orderEvents.Where(x=>x.Status.IsFill()).Aggregate(...)`로 수량가중평균을 다시 계산해 `FillState`(불변 레코드)로 교체(OrderTicket.cs:504-551) — race condition 방지를 위해 매번 새 불변 객체를 만든다.
- Option 행사(`OptionExercise`)는 별도 분기: ITM이면 `Price = StrikePrice`로 고정하고, fill price가 0이 아닐 때만 수량에 반영(OTM은 fill price 0 가정).
- `OrderClosed`(`ManualResetEvent`)로 동기 대기가 가능 — `ticket.OrderClosed.WaitOne()` 패턴으로 알고리즘이 체결을 기다릴 수 있다.
- 존재하지 않는 주문 ID로 취소/업데이트를 시도하면 `OrderTicket.InvalidCancelOrderId`/`InvalidUpdateOrderId`(618-643)가 `OrderStatus.Invalid`로 오버라이드된 "가짜 티켓"을 반환한다 — null을 리턴하지 않고 항상 티켓 객체를 준다는 API 계약.

### 1.5 `OrderEvent` (Common/Orders/OrderEvent.cs)

ProtoBuf 직렬화 대상(`[ProtoContract]`) — 라이브 결과 스트리밍/영속화를 고려한 설계. `FillPrice`, `FillQuantity` 등도 `Normalize()`를 거친다. ticket에 event가 쌓일 때마다 `orderEvent.Id = order.GetNewId()`로 order별 증분 이벤트 ID가 부여된다(스레드세이프 `Interlocked.Increment`, Order.cs:338-341).

### 1.6 상태 전이 요약

```
New --(브로커 제출 성공)--> Submitted --(부분체결)--> PartiallyFilled --(완전체결)--> Filled
New --(검증 실패/브로커 거부)--> Invalid
Submitted/PartiallyFilled --(CancelOrder 요청)--> CancelPending --(브로커 확인)--> Canceled
                                                                --(취소 실패)--> 원상태로 롤백(CancelPendingOrders.RemoveAndFallback)
Submitted/PartiallyFilled --(UpdateOrder 요청 성공)--> UpdateSubmitted
```

`BrokerageTransactionHandler.HandleOrderEvents`(1233-1236)에서 특이한 방어 로직 발견:

```csharp
// Engine/TransactionHandlers/BrokerageTransactionHandler.cs:1230-1236
if (order.Status != OrderStatus.Filled && order.Status != OrderStatus.Canceled || orderEvent.Status != OrderStatus.Invalid)
{
    order.Status = orderEvent.Status;
}
```
즉 "이미 Filled/Canceled인 주문에 대해 뒤늦게 Invalid 이벤트가 도착해도 상태를 되돌리지 않는다" — 브로커 측 race condition(업데이트가 체결 후 거부되는 경우)에 대한 방어.

**AIOS 시사점**
- Order 필드를 `internal set`으로 완전히 캡슐화하고 상태 변경 경로를 단일 핸들러로 강제하는 설계는 AIOS의 주문 무결성 보장에 직접 참고 가능 — "누가 order를 mutate할 수 있는가"를 아키텍처 수준에서 봉쇄.
- OrderRequest가 자신의 Response/Status를 들고 다니는 패턴은 요청-응답 추적을 위한 별도 correlation table 없이도 감사 추적(audit trail)이 가능하게 함 — AIOS 주문 감사 로그 설계에 참고.
- "이미 종료 상태인 주문은 되돌리지 않는다"는 방어적 상태 전이 규칙은 비동기 브로커 이벤트가 뒤섞여 도착하는 실전 환경에서 필수 — AIOS도 상태머신에 idempotency/단조성(monotonicity) 가드를 명시적으로 넣어야 함.
- `CancelPending`을 "open"으로 유지해 마진 재계산에 반영하는 것은 "취소 요청 중에도 자금이 묶여 있다"는 현실을 반영 — AIOS의 buying power 계산에서도 in-flight cancel을 놓치지 않아야 함.

---

## 2. Transaction Handler (Engine/TransactionHandlers/BrokerageTransactionHandler.cs, 2021 lines)

### 2.1 아키텍처 개요

`BrokerageTransactionHandler`는 `ITransactionHandler`를 구현하며 알고리즘과 브로커 사이의 유일한 중재자다. 핵심 자료구조(43-118행):

```csharp
private readonly ConcurrentDictionary<int, Order> _completeOrders;
private readonly ConcurrentDictionary<int, OpenOrderState> _openOrders; // New/Submitted/PartiallyFilled/CancelPending
private readonly ConcurrentDictionary<int, OrderTicket> _completeOrderTickets;
private readonly ConcurrentQueue<OrderEvent> _orderEvents;
protected CancelPendingOrders _cancelPendingOrders; // CancelPending 추적 전용 보조 구조체
private OrderRequestProcessingPool _threadPool; // per-order 순서를 보장하는 워커 스레드 풀
```

`_openOrders`와 `_completeOrders`를 분리해 "아직 살아있는 주문"과 "닫힌 주문"의 조회 성능을 분리한 것이 특징 — `TryGetOrder`(630-647)는 먼저 `_openOrders`를 보고, 없으면 `_completeOrders`+`_completeOrderTickets`를 조회한다.

### 2.2 요청 처리 흐름 (Process → AddOrder/UpdateOrder/CancelOrder → 스레드 풀 → HandleXxxRequest)

`Process(OrderRequest)`(302-325)는 요청 타입별로 디스패치한다. **AddOrder**(332-399)의 핵심 순서:

1. Shortable 체크(공매도 가능 여부) → 실패 시 라이브에서는 경고만, 백테스트는 즉시 에러.
2. `Order.CreateOrder(request)`로 order 객체 생성, ticket과 매핑을 `_completeOrderTickets`에 즉시 등록(성공/실패 무관하게).
3. 검증 통과 시 `_openOrders[order.Id] = new OpenOrderState(...)` 등록 후 `_threadPool.Dispatch(request, order)` — 여기서 실제 처리는 **비동기 워커 스레드**로 넘어간다.
4. `WaitForOrderSubmission(ticket)`(405-419)은 동기 주문(`Asynchronous=false`)에 한해 **최대 1초** `ticket.OrderSet.WaitOne(Time.OneSecond)`로 대기 — 타임아웃 시 에러 로그만 남기고 진행(예외를 던지지 않음, best-effort).

`HandleSubmitOrderRequest`(876-984)가 실제 워커 스레드에서 실행되는 로직: lot size 반올림(`RoundOffOrder`) → combo order 완결성 체크(`TryGetGroupOrders`) → 가격 tick-size 반올림(`RoundOrderPrices`) → order property sanitize → **buying power 검증**(`HasSufficientBuyingPowerForOrders`) → `_algorithm.BrokerageModel.CanSubmitOrder` 체크 → `_brokerage.PlaceOrder(o)` 호출. 실패 시 `InvalidateOrders`로 그룹 전체를 Invalid 처리(콤보 주문의 원자성 보장).

`HandleCancelOrderRequest`(1087-1136)와 `HandleUpdateOrderRequest`(989-1082)도 동일 패턴 — 상태 검증(New/closed 여부) → 브로커 호출 → 실패 시 `_cancelPendingOrders.RemoveAndFallback(order)`로 CancelPending 상태를 원래 상태로 롤백.

### 2.3 주문 ID vs 브로커 ID

내부 order id는 `algorithm.Transactions.GetIncrementOrderId()`(엔진 전역 카운터)로 발급되는 정수. 브로커가 부여하는 ID는 `Order.BrokerId`(`List<string>`, 브로커가 하나의 주문을 여러 조각으로 split할 수 있어 리스트)에 별도 저장된다. 브로커가 사후에 이 ID를 바꾸면(`BrokerageOrderIdChangedEvent`) 다음과 같이 전체 컬렉션을 교체한다:

```csharp
// Engine/TransactionHandlers/BrokerageTransactionHandler.cs:1520-1539
private void HandlerBrokerageOrderIdChangedEvent(BrokerageOrderIdChangedEvent brokerageOrderIdChangedEvent)
{
    var originalOrder = GetOrderByIdInternal(brokerageOrderIdChangedEvent.OrderId);
    if (originalOrder == null) { Log.Error(...); return; }
    originalOrder.BrokerId = brokerageOrderIdChangedEvent.BrokerId;
}
```
`GetOrdersByBrokerageId(string)`(654-669)로 브로커 ID → Lean order 역참조가 가능. 알 수 없는(unknown) 주문 이벤트가 들어오면 `TryGetOrder`가 실패하고 `Log.Error("Unable to locate order or ticket for order ID ...")` 후 해당 이벤트는 드롭된다(HandleOrderEvents:1205-1210) — 즉 **미지의 주문 이벤트는 조용히 버려짐**(예외 전파 없음).

### 2.4 `HandleOrderEvents` — 체결/현금/보유 업데이트 파이프라인 (1185-1416)

배치(List<OrderEvent>) 단위로 처리되며 `_lockHandleOrderEvent`로 전체 직렬화한다(스레드풀이 여러 개라도 이 블록은 단일 스레드처럼 동작). 순서:

1. 각 이벤트에 대해 order/ticket/security 조회, closed 상태면 `_openOrders`에서 제거하고 `_completeOrders`로 이관.
2. `order.Status = orderEvent.Status` 갱신(단, 위 2.6절의 "역행 방지" 가드 적용), `CanceledTime`/`LastFillTime`/`LastUpdateTime` 타임스탬프 기록.
3. Crypto 현물 매수 시 수수료가 base currency로 부과되는 경우 `FillQuantity`에서 수수료를 차감(가상 포지션이 실제 잔고보다 커지는 것 방지, 1302-1320).
4. **`_algorithm.Portfolio.ProcessFills(fillsToProcess)`** — 실제 현금/보유 반영은 여기서 일괄 처리(포트폴리오 매니저 위임).
5. `_algorithm.TradeBuilder.ProcessFill(...)` — 거래 통계/트레이드 로그용 별도 기록.
6. 마지막으로 `ticket.AddOrderEvent(orderEvent)` (fill 반영 *이후*, 사용자 이벤트 발생 *이전*).
7. 락 해제 후 `_resultHandler.OrderEvent`, `NewOrderEvent`, `_executionModel.OnOrderEvent`, `_algorithm.OnOrderEvent(orderEvent)` 순으로 통지. `OnOrderEvent` 핸들러가 10초(`SlowOnOrderEventThreshold`) 이상 걸리면 1회 경고를 로그+algorithm.Debug로 발송(1387-1404) — 사용자 콜백이 트랜잭션 스레드/GIL을 막는 것에 대한 관측성 장치.

### 2.5 RoundOff / 가격 검증

```csharp
// Engine/TransactionHandlers/BrokerageTransactionHandler.cs:1784-1805
public decimal RoundOffOrder(Order order, Security security)
{
    var orderLotMod = order.Quantity % security.SymbolProperties.LotSize;
    if (orderLotMod != 0) { order.Quantity -= orderLotMod; /* 최초 1회 경고 */ }
    return order.Quantity;
}
```
가격은 `RoundOrderPrices`(1833-1931)가 `security.PriceVariationModel.GetMinimumPriceVariation(...)`로 tick size에 맞춰 반올림(`Math.Round(price/increment)*increment`). Combo(`ComboLimit`) 주문은 전체 레그의 최소 tick 증분을 찾아 그룹 limit price에 적용한다.

### 2.6 타임아웃, 큐, 동시성

- `OrderRequestProcessingPool`(515 lines, Engine/TransactionHandlers/OrderRequestProcessingPool.cs): 단일 공유 큐에서 여러 워커 스레드가 가져가되, **동일 주문 ID의 요청 순서는 보장**하며 부하에 따라 스레드 수를 `MinimumTransactionThreads`(기본 2) ~ `MaximumTransactionThreads`(기본 10, config로 조절)까지 동적으로 늘린다.
- `ProcessSynchronousEvents`(729-784): 백테스트에서는 큐가 빌 때까지 최대 1초 대기(`_threadPool.WaitForProcessing(Time.OneSecond)`), 라이브에서는 현금 동기화 체크(10초 이상 fill이 없을 때만) + 오래된 주문(10,000건 초과분) 정리.
- **최대 재시도**: `MaxCashSyncAttempts = 5` — 연속 5회 현금 동기화 실패 시 예외를 던져 알고리즘을 중단시킨다(747-756행 부근, `_failedCashSyncAttempts`).
- **주문 개수 제한**: 실제로는 `BrokerageTransactionHandler`가 아니라 `Algorithm/QCAlgorithm.Trading.cs:1156-1163`에 있음:
```csharp
private int _maxOrders = 10000; // QCAlgorithm.Trading.cs:32
if (!LiveMode && Transactions.OrdersCount > _maxOrders)
{
    Status = AlgorithmStatus.Stopped;
    return OrderResponse.Error(request, OrderResponseErrorCode.ExceededMaximumOrders, ...);
}
```
**백테스트에만 적용**(라이브는 무제한), 클라우드 계정 등급에 따라 `SetMaximumOrders(int)`로 조정 가능한 것으로 보인다(1443행에서 `_maxOrders = max`).

**AIOS 시사점**
- open/complete order를 별도 dictionary로 분리하는 것은 hot-path(활성 주문 조회)와 cold-path(이력 조회)의 성능 분리에 유효한 패턴 — AIOS 주문 저장소 설계에 반영 가능.
- "동일 주문 ID의 요청은 순서 보장, 서로 다른 주문은 병렬" 스레드풀 모델은 순서 의존적 side-effect(취소 후 갱신 등)를 안전하게 병렬화하는 좋은 참조 설계.
- 브로커 ID를 `List<string>`으로 보유해 "한 논리 주문이 여러 브로커 조각으로 분할"되는 현실을 1급 시민으로 모델링한 점 — AIOS도 parent/child order 분할(iceberg, 알고 실행 등)을 브로커 ID 레벨에서부터 고려해야 함.
- 미지의 주문 이벤트를 조용히 드롭하는 정책은 편리하지만 감사 관점에서는 위험 — AIOS에서는 최소한 dead-letter 큐나 알림으로 노출해야 할 대상.

---

## 3. Brokerage 추상화 계층

### 3.1 `IBrokerage` (Common/Interfaces/IBrokerage.cs)

이벤트 기반 인터페이스. 핵심 이벤트 9종: `OrderIdChanged`, `OrdersStatusChanged`(`List<OrderEvent>` 배치), `OrderUpdated`(가격 변경 등 상태변화 아닌 업데이트), `OptionPositionAssigned`, `OptionNotification`, `NewBrokerageOrderNotification`(브로커 측에서 발생한 주문), `DelistingNotification`, `AccountChanged`, `Message`(범용 브로커 메시지).

메서드는 `PlaceOrder`/`UpdateOrder`/`CancelOrder`(모두 `bool` 반환 — "요청이 접수되었는가"이지 "체결되었는가"가 아님), `GetOpenOrders`/`GetAccountHoldings`/`GetCashBalance`(재시작 시 reconciliation용), `GetHistory`(백테스트/라이브 겸용 히스토리 조회), `ConcurrencyEnabled`(브로커별 동시요청 처리 가능 여부 스위치).

### 3.2 `Brokerage` 베이스 클래스 (Brokerages/Brokerage.cs, 939 lines)

이벤트 invocation 헬퍼를 제공, 각 구체 브로커는 `OnOrderEvent(e)`, `OnMessage(e)` 등을 호출하기만 하면 된다(직접 이벤트 필드 접근 불필요):

```csharp
// Brokerages/Brokerage.cs:184-187
protected virtual void OnOrderEvent(OrderEvent e)
{
    OnOrderEvents(new List<OrderEvent> { e });
}
```

**현금 동기화**(`ShouldPerformCashSync`/`PerformCashSync`, 519-640, 8절에서 상세)도 베이스 클래스에 구현되어 있어 개별 브로커 구현체는 `GetCashBalance()`만 정확히 구현하면 동기화 로직을 재사용한다.

### 3.3 `BrokerageMessageEvent` / `IBrokerageMessageHandler`

```csharp
// Common/Brokerages/BrokerageMessageEvent.cs:44-49, 69-82
public BrokerageMessageEvent(BrokerageMessageType type, int code, string message) {...}
public static BrokerageMessageEvent Disconnected(string message) => new(..., DisconnectCode, message);
public static BrokerageMessageEvent Reconnected(string message) => new(..., ReconnectCode, message);
```
`BrokerageMessageType`(Warning/Error/Information/Reconnect/Disconnect 추정)로 분류되고, `DefaultBrokerageMessageHandler`/`DowngradeErrorCodeToWarningBrokerageMessageHandler`(파일 존재 확인)가 메시지를 알고리즘 상태 전이(예: Disconnect → 재연결 대기)로 매핑하는 정책 객체.

### 3.4 `IBrokerageModel` — 백테스트/라이브 동일 동작(parity)의 핵심 (Common/Brokerages/IBrokerageModel.cs, 431 lines)

이 인터페이스가 "브로커의 규칙(허용 주문 유형/사이즈/레버리지)"과 "브로커의 시뮬레이션 모델(fee/fill/slippage/settlement/margin)"을 하나로 묶어, **백테스트 엔진이 실제 브로커와 동일한 제약·비용 모델을 재현**하도록 강제한다:

```csharp
bool CanSubmitOrder(Security security, Order order, out BrokerageMessageEvent message);
bool CanUpdateOrder(Security security, Order order, UpdateOrderRequest request, out BrokerageMessageEvent message);
bool CanExecuteOrder(Security security, Order order);
IFillModel GetFillModel(Security security);
IFeeModel GetFeeModel(Security security);
ISlippageModel GetSlippageModel(Security security);
ISettlementModel GetSettlementModel(Security security);
IBuyingPowerModel GetBuyingPowerModel(Security security);
IMarginInterestRateModel GetMarginInterestRateModel(Security security);
IShortableProvider GetShortableProvider(Security security);
decimal GetLeverage(Security security);
void ApplySplit(List<OrderTicket> tickets, Split split);
```

`DefaultBrokerageModel`(Common/Brokerages/DefaultBrokerageModel.cs, 407 lines)이 기본 구현을 제공하고, 60여 개의 브로커별 서브클래스(`InteractiveBrokersBrokerageModel`, `AlpacaBrokerageModel`, `BinanceBrokerageModel` 등, `Common/Brokerages/*BrokerageModel.cs`)가 이를 override한다. 예:

```csharp
// Common/Brokerages/DefaultBrokerageModel.cs:103-114
public virtual bool CanSubmitOrder(Security security, Order order, out BrokerageMessageEvent message)
{
    if ((security.Type == SecurityType.Future || security.Type == SecurityType.FutureOption)
        && order.Type == OrderType.MarketOnOpen)
    {
        message = new BrokerageMessageEvent(BrokerageMessageType.Warning, "NotSupported",
            Messages.DefaultBrokerageModel.UnsupportedMarketOnOpenOrdersForFuturesAndFutureOptions);
        return false;
    }
    message = null;
    return true;
}
```

```csharp
// Common/Brokerages/DefaultBrokerageModel.cs:222-245  GetFillModel: SecurityType별 분기
case SecurityType.Equity: return new EquityFillModel();
case SecurityType.FutureOption: return new FutureOptionFillModel();
case SecurityType.Future: return new FutureFillModel();
default: return new ImmediateFillModel();
```

`BrokerageTransactionHandler`는 주문 제출/업데이트 시점에 `_algorithm.BrokerageModel.CanSubmitOrder`/`CanUpdateOrder`를 직접 호출해 이 게이트를 강제한다(2.2절 참고) — **백테스트와 라이브가 동일한 코드 경로에서 동일한 `IBrokerageModel` 인스턴스를 사용**하기 때문에, "라이브에서만 발생하는 거부"를 백테스트 단계에서 미리 재현할 수 있다는 것이 LEAN의 parity 전략의 핵심.

**AIOS 시사점**
- "브로커 제약(Can*) + 시뮬레이션 모델(Get*Model) 번들"을 하나의 인터페이스로 묶는 설계는 백테스트-라이브 parity를 아키텍처로 강제하는 가장 강력한 방법 — AIOS의 브로커 어댑터도 단순 주문 전송기가 아니라 "제약+비용 모델 제공자"로 설계해야 함.
- 이벤트 기반 `IBrokerage` + 정책 객체(`IBrokerageMessageHandler`)로 연결 단절/재연결을 분리한 것은 라이브 안정성 확보에 유효한 패턴.
- `PlaceOrder`가 "접수 성공"만 보장하고 체결은 별도 비동기 이벤트(`OrdersStatusChanged`)로 온다는 명확한 계약 분리는 AIOS의 브로커 인터페이스 설계 시 반드시 지켜야 할 원칙(동기 반환값과 비동기 체결을 혼동하지 않기).

---

## 4. Fee / Fill / Slippage 모델

### 4.1 3계층 비용 모델의 분리

`IBrokerageModel.GetFeeModel/GetFillModel/GetSlippageModel/GetSettlementModel`을 통해 **Security별로 서로 다른 모델을 주입**할 수 있다 — 즉 하나의 알고리즘 내에서 Equity는 `EquityFillModel`+`InteractiveBrokersFeeModel`, Crypto는 다른 조합을 쓸 수 있다.

### 4.2 Fee: `IFeeModel` (Common/Orders/Fees/IFeeModel.cs)

```csharp
public interface IFeeModel
{
    OrderFee GetOrderFee(OrderFeeParameters parameters);
}
```
`OrderFee`(Common/Orders/Fees/OrderFee.cs)는 `CashAmount`(통화+금액)를 감싸고, `ApplyToPortfolio`가 `portfolio.CashBook[Value.Currency].AddAmount(-Value.Amount)`로 직접 현금에 차감한다 — fee가 주문 통화가 아닌 별도 통화로 청구될 수 있음을 1급으로 지원(예: 암호화폐 fee가 base currency로 청구). 34개 브로커별 fee 모델(`InteractiveBrokersFeeModel`, `BinanceFeeModel`, `AlpacaFeeModel` 등)이 존재하며 계층/수량/명목가 기반 tiered fee를 구현한다.

### 4.3 Fill: `FillModel` (Common/Orders/Fills/FillModel.cs, 1262 lines) — 실제 구현의 대부분이 위치

`ImmediateFillModel`은 사실상 `FillModel`을 override 없이 상속만 하는 껍데기(Common/Orders/Fills/ImmediateFillModel.cs, 23 lines 전체) — 즉 "기본 fill 로직 = ImmediateFillModel"이며 이름과 달리 실제 로직은 base class에 있다.

`FillModel.MarketFill`(273-338)의 핵심 로직:
```csharp
// Common/Orders/Fills/FillModel.cs:315-332
fill.FillPrice = GetMarketFillPrice(asset, order, prices, subscriptionConfigs);
fill.Status = OrderStatus.Filled;
var slip = asset.SlippageModel.GetSlippageApproximation(asset, order);
switch (orderDirection)
{
    case OrderDirection.Buy: fill.FillPrice += slip; break;
    case OrderDirection.Sell: fill.FillPrice -= slip; break;
}
fill.FillQuantity = quantity; // 항상 전량 체결 가정 (base FillModel)
```
Stale 데이터 방어: 마지막 데이터의 종료시각이 주문 시각보다 `StalePriceTimeSpan` 이상 뒤처져 있으면(해상도 기준 1봉 이상 지연) 신선한 데이터가 올 때까지 체결을 보류(`ShouldWaitForFreshDataOnStale`)하거나, 그렇지 않으면 stale 가격으로 체결하되 메시지에 경고를 남긴다(304-313).

`StopMarketFill`(347-398)은 최악 시나리오(worse-case) 가정 — Sell Stop은 `Math.Min(order.StopPrice, prices.Current - slip)`, Buy Stop은 `Math.Max(order.StopPrice, prices.Current + slip)`으로, **항상 트레이더에게 불리한 쪽으로 체결가를 잡는다**(보수적 백테스트).

### 4.4 `EquityFillModel` (Common/Orders/Fills/EquityFillModel.cs, 1100 lines) — Bid/Ask 기반 정밀화

base `FillModel`이 last-trade 가격(`TradeBar.Close`류)을 쓰는 반면, `EquityFillModel.MarketFill`(124-179)은 **호가(quote) 데이터**를 우선 사용:
```csharp
// Common/Orders/Fills/EquityFillModel.cs:150-160
case OrderDirection.Buy:
    fillPrice = GetBestEffortAskPrice(asset, order.Time, out fillMessage, out stalePrice, out staleDataEndTimeUtc, subscribedTypes) + slip;
    break;
case OrderDirection.Sell:
    fillPrice = GetBestEffortBidPrice(asset, order.Time, out fillMessage, out stalePrice, out staleDataEndTimeUtc, subscribedTypes) - slip;
    break;
```
즉 매수는 ask, 매도는 bid에 체결 — 스프레드를 자동으로 반영하는 현실적 시뮬레이션. `LimitFill`, `StopLimitFill`, `MarketOnOpenFill`, `MarketOnCloseFill` 등 7개 주문유형 전부를 override하여 Equity 전용 미시구조 가정을 적용한다.

### 4.5 Slippage: `ISlippageModel` (Common/Orders/Slippage/ISlippageModel.cs)

단일 메서드 `decimal GetSlippageApproximation(Security asset, Order order)`. `VolumeShareSlippageModel`(Common/Orders/Slippage/VolumeShareSlippageModel.cs)은 주문량/바 거래량 비율의 제곱에 비례하는 price-impact 모델:
```csharp
// Common/Orders/Slippage/VolumeShareSlippageModel.cs:46,84-89
var volumeShare = Math.Min(order.AbsoluteQuantity / barVolume, _volumeLimit); // 기본 volumeLimit=2.5%
slippagePercent = volumeShare * volumeShare * _priceImpact; // 기본 priceImpact=0.1
return slippagePercent * lastData.Value;
```
거래량이 0 이하일 때 FX/CFD/Crypto는 슬리피지 0(거래량 데이터 부재를 인정), 그 외 자산은 최대 슬리피지 비율을 적용 — "유동성 부재 = 무한대 임팩트"라는 보수적 원칙을 코드로 명시. `ConstantSlippageModel`, `MarketImpactSlippageModel`, `NullSlippageModel`도 존재(옵션형).

**AIOS 시사점**
- Fee/Fill/Slippage/Settlement를 Security 단위로 독립 주입 가능한 4개의 별도 인터페이스로 분리한 것은 "백테스트 리얼리즘 게이트"를 구성 요소별로 검증 가능하게 만드는 좋은 참조 아키텍처 — AIOS 백테스트 엔진의 리얼리즘 체크리스트(fee 모델 명시 여부, fill 모델의 스프레드 반영 여부, 최소 슬리피지 하한 등)를 여기서 도출 가능.
- "유동성 부족 시 슬리피지 무한대로 처리"라는 보수적 기본값은 AIOS가 채택할 만한 안전장치 — 사용자가 명시적으로 override하지 않는 한 낙관적 가정을 기본값으로 두지 않는 원칙.
- Stop 주문 체결가를 항상 트레이더에게 불리하게 계산(worst-case)하는 것은 백테스트 과최적화(over-optimistic backtest) 방지를 위한 핵심 원칙 — AIOS 리얼리즘 게이트의 필수 항목으로 채택 권장.
- `EquityFillModel`이 bid/ask 데이터 유무에 따라 fill 정밀도가 달라지는 구조는, AIOS에서도 "quote 데이터가 없으면 자동으로 덜 정밀한 fallback 모델로 전환"하는 명시적 계약이 필요함을 시사.

---

## 5. Risk Management / Execution Framework

### 5.1 Algorithm Framework 5단계 파이프라인에서의 위치

LEAN의 Algorithm Framework는 `Universe Selection → Alpha → Portfolio Construction → Risk Management → Execution`의 5단계로 구성되며, 매 알고리즘 타임스텝마다 순서대로 실행된다. `RiskManagementModel`은 Portfolio Construction이 만든 `IPortfolioTarget[]`을 받아 **필터링/축소**하고, `ExecutionModel`은 최종 target을 실제 주문으로 변환한다.

### 5.2 `MaximumDrawdownPercentPortfolio` (Algorithm.Framework/Risk/MaximumDrawdownPercentPortfolio.cs)

```csharp
// 52-84행
public override IEnumerable<IPortfolioTarget> ManageRisk(QCAlgorithm algorithm, IPortfolioTarget[] targets)
{
    var currentValue = algorithm.Portfolio.TotalPortfolioValue;
    if (!_initialised) { _portfolioHigh = currentValue; _initialised = true; }
    if (_isTrailing && (_portfolioHigh < currentValue)) { _portfolioHigh = currentValue; yield break; }
    var pnl = GetTotalDrawdownPercent(currentValue);
    if (pnl < _maximumDrawdownPercent && targets.Length != 0)
    {
        _initialised = false; // 재시작 허용
        foreach (var target in targets)
        {
            algorithm.Insights.Cancel(new[] { target.Symbol });
            yield return new PortfolioTarget(target.Symbol, 0); // 강제 청산
        }
    }
}
```
`isTrailing=false`(기본값)면 시작 자본 대비 고정 낙폭 기준, `true`면 최고점 대비 trailing 낙폭. 트리거되면 `Insights.Cancel`로 알파 신호까지 무효화하고 전량 청산 타겟(quantity=0)을 반환 — **한 번 트리거되면 이후 리스크 모델이 자동으로 재개**(`_initialised=false`로 리셋)되지만, 문서 주석에는 "수동 재시작 필요"라고 명시되어 있어 실제 동작과 문서가 다소 불일치하는 부분으로 보인다.

### 5.3 Execution Models — Algorithm 타임스텝과의 상호작용

`IExecutionModel.Execute(QCAlgorithm, IPortfolioTarget[] targets)`가 유일한 진입점(Algorithm/Execution/IExecutionModel.cs). `ImmediateExecutionModel`(Algorithm/Execution/ImmediateExecutionModel.cs, 86 lines)은 target을 받는 즉시 시장가 주문을 낸다:

```csharp
// Algorithm/Execution/ImmediateExecutionModel.cs:45-75
public override void Execute(QCAlgorithm algorithm, IPortfolioTarget[] targets)
{
    _targetsCollection.AddRange(targets);
    if (!_targetsCollection.IsEmpty)
    {
        foreach (var target in _targetsCollection.OrderByMarginImpact(algorithm))
        {
            var quantity = OrderSizing.GetUnorderedQuantity(algorithm, target, security, true);
            if (quantity != 0 && security.BuyingPowerModel.AboveMinimumOrderMarginPortfolioPercentage(...))
            {
                algorithm.MarketOrder(security, quantity, Asynchronous, target.Tag);
            }
        }
        _targetsCollection.ClearFulfilled(algorithm);
    }
}
```
`OrderByMarginImpact`(마진 여유가 큰 순 → 매도부터 처리해 buying power를 먼저 확보하는 순서 최적화로 추정), `ClearFulfilled`(이미 목표 수량에 도달한 target 제거)로 **타겟 컬렉션이 여러 타임스텝에 걸쳐 누적**되며 매 스텝 재평가된다 — 즉 execution model은 stateful하며 "미체결 잔여 목표"를 스스로 추적한다.

`VolumeWeightedAveragePriceExecutionModel`(Algorithm.Framework/Execution/VolumeWeightedAveragePriceExecutionModel.cs)은 VWAP 지표(`SymbolData` 내부에 인디케이터 보유)보다 유리한 가격일 때만 주문을 내고(`PriceIsFavorable`), `OrderSizing.GetOrderSizeForPercentVolume`(기본 1%)로 바당 최대 주문 크기를 제한 — **알고리즘 타임스텝마다 부분적으로만 주문을 분할 실행**하는 TWAP/VWAP형 알고리즘 실행 전략. `StandardDeviationExecutionModel`은 가격이 이동평균 대비 표준편차 밴드를 벗어날 때만 실행하는 변동성 기반 타이밍 모델.

**AIOS 시사점**
- Risk Management를 "target을 필터링/override하는 순수 함수형 단계"로 분리하고 Execution을 별도 단계로 두는 5단계 파이프라인은 관심사 분리가 명확 — AIOS도 "무엇을 보유해야 하는가(target)"와 "어떻게 도달하는가(execution)"를 동일하게 분리 권장.
- Execution model이 상태(누적 target, VWAP 인디케이터)를 스스로 관리하며 매 타임스텝 재호출되는 구조는 알고리즘 실행(algo execution) 전략을 "긴 시간에 걸친 상태 머신"으로 모델링하는 참고 사례.
- MaximumDrawdown 리스크 모델의 문서-동작 불일치(자동 재개 vs "수동 재시작 필요") 같은 사례는, AIOS 문서화 시 실제 코드 동작을 반드시 재검증해야 함을 보여주는 반면교사.

---

## 6. Real-Time / Scheduling — 백테스트 vs 라이브 루프의 차이

### 6.1 `IRealTimeHandler` 공통 계약 (Engine/RealTime/IRealTimeHandler.cs)

```csharp
void Setup(IAlgorithm algorithm, AlgorithmNodePacket job, IResultHandler resultHandler, IApi api, IIsolatorLimitResultProvider isolatorLimitProvider);
void SetTime(DateTime time);       // "같은 코드로 백테스트/라이브 이벤트를 처리"하기 위한 추상화
void ScanPastEvents(DateTime time); // 데이터 부재로 못 쐈던 과거 이벤트를 보정
void Exit();
```
주석에 명시: `SetTime`은 "so we can use same code for backtesting and live events".

### 6.2 백테스트: `BacktestingRealTimeHandler` — 데이터 시간에 종속

```csharp
// Engine/RealTime/BacktestingRealTimeHandler.cs:100-119
public override void SetTime(DateTime time)
{
    var scheduledEvents = GetScheduledEventsSortedByTime();
    while (scheduledEvents.Count > 0 && scheduledEvents[0].NextEventUtcTime <= time)
    {
        IsolatorLimitProvider.Consume(scheduledEvents[0], time, TimeMonitor);
        SortFirstElement(scheduledEvents);
    }
}
```
별도 스레드가 없다 — 데이터 피드가 `SetTime(barTime)`을 호출할 때마다 "이 시각까지 발생했어야 할 예약 이벤트"를 동기적으로 모두 소진(catch-up)한다. `ScanPastEvents`(125-149)는 한 걸음 더 나가 `Algorithm.SetDateTime(nextEventUtcTime)`으로 **알고리즘 시계 자체를 이벤트 시각으로 되감아** 이벤트를 발생시킨 뒤 다음 이벤트로 진행 — 데이터가 듬성듬성해서 여러 스케줄 이벤트가 한 데이터 틱 사이에 몰려 있어도 순서대로 모두 재현하기 위함.

### 6.3 라이브: `LiveTradingRealTimeHandler` — 실제 벽시계 스레드

```csharp
// Engine/RealTime/LiveTradingRealTimeHandler.cs:93-120
private void Run()
{
    IsActive = true;
    while (!_cancellationTokenSource.IsCancellationRequested)
    {
        var time = TimeProvider.GetUtcNow();
        WaitTillNextSecond(time);
        foreach (var kvp in ScheduledEvents.OrderBySafe(pair => pair.Value))
        {
            IsolatorLimitProvider.Consume(kvp.Key, time, TimeMonitor);
        }
    }
    IsActive = false;
}
```
`SetTime`(126-139)은 라이브에서는(워밍업이 아닌 한) **별도의 백그라운드 스레드**(`Thread(Run){IsBackground=true, Name="RealTime Thread"}`)를 한 번 기동시킬 뿐, 이후 데이터 피드의 `SetTime` 호출은 무시된다(`else if (_realTimeThread == null)` 가드로 최초 1회만). 즉 라이브 모드는 **초 단위 벽시계**(`WaitTillNextSecond`)로 자체 폴링하며, 데이터 도착 여부와 무관하게 스케줄 이벤트를 정확한 시각에 발화한다. `ScanPastEvents`는 라이브에서는 아무것도 하지 않음(no-op) — 라이브에는 "과거"가 없기 때문.

### 6.4 시장 시간 처리

`ScheduledEventFactory.cs`(167 lines)가 `Exchange.Hours`(마켓 오픈/클로즈) 기반 이벤트 팩토리를 제공(`EveryDayAt`, `BeforeMarketClose`, `AfterMarketOpen` 등으로 추정되는 패턴 — Algorithm 쪽 Schedule API와 연결).

**AIOS 시사점**
- "백테스트=데이터 시간에 동기적으로 종속, 라이브=독립된 벽시계 스레드"라는 이원화는 필연적이지만, **동일한 `IRealTimeHandler` 인터페이스**로 알고리즘 코드가 두 모드 차이를 몰라도 되게 만든 것이 핵심 설계 가치 — AIOS의 스케줄링 계층도 백테스트/라이브 겸용 단일 API를 목표로 해야 함.
- 백테스트의 `ScanPastEvents`가 "데이터가 듬성듬성해도 예약 이벤트 순서를 놓치지 않는다"는 보정 로직은, 저빈도 데이터(예: 일봉)로 고빈도 스케줄 이벤트를 백테스트할 때 흔히 발생하는 버그 클래스를 미리 방지 — AIOS 백테스트 엔진에도 유사한 "누락 이벤트 재생" 메커니즘이 필요.
- 라이브 스레드가 초 단위로 전체 예약 이벤트를 순회(`OrderBySafe`)하는 방식은 이벤트 수가 많아지면 O(n) 스캔 비용이 매초 발생 — AIOS는 이 부분을 min-heap 등으로 최적화할 여지가 있음(LEAN도 `SortFirstElement`로 부분 최적화는 하고 있으나 매초 전체 순회는 동일).

---

## 7. Results / Statistics

### 7.1 계산되는 통계량 (Common/Statistics/PortfolioStatistics.cs)

`PortfolioStatistics` 생성자(약 280-320행)에서 한 번에 계산:

| 통계량 | 필드 | 비고 |
|---|---|---|
| CAGR | `CompoundingAnnualReturn` | `Statistics.CompoundingAnnualPerformance(...)` |
| 최대 낙폭 | `Drawdown`, `DrawdownRecovery` | `Statistics.CalculateDrawdownMetrics(equity, 3)` (3 = 반올림 자리수로 추정) |
| Sharpe | `SharpeRatio` | 무위험이자율 대비 초과수익/연변동성 |
| **Probabilistic Sharpe Ratio (PSR)** | `ProbabilisticSharpeRatio` | 아래 4.2절 |
| Sortino | `SortinoRatio` | 하방편차 기준 |
| Alpha / Beta | `Alpha`, `Beta` | 벤치마크 대비 CAPM 회귀 |
| Information Ratio | `InformationRatio` | 트래킹 에러 대비 초과수익 |
| Treynor Ratio | `TreynorRatio` | Beta 대비 초과수익 |
| VaR | (Variance-covariance 1-day VaR) | 175-182행 근처 |

PSR 산식(Common/Statistics/Statistics.cs:200-223):
```csharp
public static double ProbabilisticSharpeRatio(List<double> listPerformance, double benchmarkSharpeRatio, double riskFreeRate = 0)
{
    var observedSharpeRatio = ObservedSharpeRatio(listPerformance, riskFreeRate);
    var skewness = listPerformance.Skewness();
    var kurtosis = listPerformance.Kurtosis();
    var operandA = skewness * observedSharpeRatio;
    var operandB = ((kurtosis - 1) / 4) * Math.Pow(observedSharpeRatio, 2);
    var estimateStandardDeviation = Math.Pow((1 - operandA + operandB) / (listPerformance.Count - 1), 0.5);
    if (double.IsNaN(estimateStandardDeviation)) return 0;
    var value = estimateStandardDeviation.IsNaNOrZero() ? 0 : (observedSharpeRatio - benchmarkSharpeRatio) / estimateStandardDeviation;
    return (new Normal()).CumulativeDistribution(value);
}
```
López de Prado의 PSR 공식(skewness/kurtosis 보정)을 그대로 구현 — 표본 수익률 분포가 정규분포에서 벗어날 때 Sharpe ratio의 과신을 보정하는 지표를 기본 통계 세트에 포함시킨 것은 LEAN이 단순 백테스트가 아닌 통계적으로 엄밀한 성과 평가를 지향함을 보여준다.

### 7.2 Backtest vs Live 결과 보고

`Engine/Results`에 `BaseResultsHandler`(1431 lines, 공통 로직), `BacktestingResultHandler`(923 lines), `LiveTradingResultHandler`(1396 lines)로 분리. `BaseResultsHandler.GenerateStatisticsResults(charts, profitLoss, estimatedStrategyCapacity)`(1149-1282)가 공통 통계 생성 진입점이며, `SamplePerformance`(773행)가 주기적으로 equity curve를 샘플링해 차트를 구성한다. 라이브 핸들러는 여기에 더해 실시간 상태 업데이트/알림 전송 책임을 추가로 지님(브로커 메시지, 런타임 에러 등을 실시간 스트리밍).

**AIOS 시사점**
- PSR을 기본 통계 세트에 포함한 것은 AIOS 백테스트 리포트의 최소 기준으로 채택할 가치가 있음 — 단순 Sharpe만으로는 표본이 짧거나 비정규분포일 때 과최적화를 걸러내지 못함.
- Backtest/Live ResultHandler를 공통 베이스(`BaseResultsHandler`)로 묶고 라이브 전용 기능만 서브클래스에 추가하는 구조는, AIOS의 리포팅 계층에서도 "생애주기 공통 통계 + 모드별 추가 기능"으로 분리할 근거가 됨.
- VaR을 covariance 방식으로만 제공하고 있어(문서상 historical/Monte-Carlo VaR 언급 없음) AIOS는 필요 시 이 부분을 보완재로 설계할 여지가 있음.

---

## 8. Reconciliation (Setup 시점 + 라이브 중 지속)

### 8.1 시작 시점: `BrokerageSetupHandler.cs` (603 lines)

`LoadCashBalance`(369-395)와 `LoadExistingHoldingsAndOrders`(400-468)가 순서대로 호출된다.

**현금 동기화(1회성, 시작 시)**:
```csharp
// Engine/Setup/BrokerageSetupHandler.cs:374-386
var cashBalance = brokerage.GetCashBalance();
foreach (var cash in cashBalance)
{
    if (!CashAmountUtil.ShouldAddCashBalance(cash, algorithm.AccountCurrency)) continue; // 0 잔고 통화 skip
    algorithm.Portfolio.SetCash(cash.Currency, cash.Amount, 0);
}
```

**보유 종목 재구성**:
```csharp
// Engine/Setup/BrokerageSetupHandler.cs:405, 420-458
GetOpenOrders(algorithm, parameters.ResultHandler, parameters.TransactionHandler, brokerage); // 미체결 주문 먼저 로드
var holdings = brokerage.GetAccountHoldings();
foreach (var holding in holdings.OrderByDescending(x => x.Type)) // Option을 먼저 처리(기초자산 정규화 모드 설정 위해)
{
    GetOrAddUnrequestedSecurity(algorithm, holding.Symbol, holding.Type, out security); // universe에 없던 종목도 즉석 등록
    security.Holdings.SetHoldings(holding.AveragePrice, holding.Quantity);
    if (holding.MarketPrice == 0) holding.MarketPrice = algorithm.GetLastKnownPrice(security)?.Price ?? 0; // 가격 워밍업
    // TradeBar를 합성해 SetMarketPrice — 실제 신규 데이터 도착 전 임시 가격
}
```
알고리즘이 명시적으로 구독하지 않은 종목(브로커 계좌에만 존재하는 레거시 포지션)도 `GetOrAddUnrequestedSecurity`로 즉석 등록해 손실 없이 반영한다 — "미지의 포지션"을 무시하지 않는 정책.

### 8.2 지속적 재동기화 (라이브 중, `Brokerage.PerformCashSync`, Brokerages/Brokerage.cs:519-640)

```csharp
// 519-530
public virtual bool ShouldPerformCashSync(DateTime currentTimeUtc)
{
    var currentTimeNewYork = currentTimeUtc.ConvertFromUtc(TimeZones.NewYork);
    if (_syncedLiveBrokerageCashToday && currentTimeNewYork.Date != LastSyncDate) _syncedLiveBrokerageCashToday = false; // 매일 자정 리셋
    return !_syncedLiveBrokerageCashToday && currentTimeNewYork.TimeOfDay >= LiveBrokerageCashSyncTime
        && Volatile.Read(ref _connectionState) == Connected;
}
```
지정된 뉴욕시간(`LiveBrokerageCashSyncTime`, 통상 새벽) 이후 하루 1회 자동 트리거. `BrokerageTransactionHandler.ProcessSynchronousEvents`(746-762)가 "최근 10초 내 체결이 없을 때만" 동기화를 요청 — 체결 도중 동기화가 레이스 컨디션을 일으키지 않도록 함.

동기화 로직 자체:
```csharp
// Brokerages/Brokerage.cs:584-609
var totalPorfolioValueThreshold = algorithm.Portfolio.TotalPortfolioValue * 0.02m; // 2% 임계값
foreach (var kvp in algorithm.Portfolio.CashBook)
{
    var balanceCash = balances.Find(balance => balance.Currency == cash.Symbol);
    if (balanceCash != default)
    {
        var delta = cash.Amount - balanceCash.Amount;
        if (... > totalPorfolioValueThreshold) Log.Trace($"... Delta: {delta:0.00}", true); // 2% 초과 괴리만 로그
        algorithm.Portfolio.CashBook[cash.Symbol].SetAmount(balanceCash.Amount); // 브로커 값으로 강제 덮어씀
    }
    else algorithm.Portfolio.CashBook[cash.Symbol].SetAmount(0); // 브로커에 없는 통화는 0 처리
}
```
**동기화 후 검증(self-verifying sync)**:
```csharp
// Brokerages/Brokerage.cs:620-637
Task.Delay(TimeSpan.FromSeconds(10)).ContinueWith(_ =>
{
    if (getTimeSinceLastFill() <= TimeSpan.FromSeconds(20))
    {
        _syncedLiveBrokerageCashToday = false; // 최근 체결이 있었다면 동기화 무효화, 재시도 예약
        Log.Trace("Unverified cash sync - resync required.");
    }
    else { Log.Trace("Verified cash sync."); algorithm.Portfolio.LogMarginInformation(); }
});
```
동기화 직후 10초를 기다렸다가, 그 사이 체결이 있었다면(레이스 가능성) 동기화를 "미검증"으로 되돌려 다음 사이클에 재시도하는 이중 확인 패턴. 연속 5회(`MaxCashSyncAttempts`) 실패 시 `BrokerageTransactionHandler`가 예외를 던져 알고리즘을 중단시킨다(2.6절 참고).

**AIOS 시사점**
- "시작 시 1회 풀 리컨실리에이션 + 라이브 중 매일 1회 자동 재동기화 + 자체 검증(10초 후 재확인) + 연속 실패 시 강제 중단"이라는 4단 방어선은 실전 리컨실리에이션 설계의 모범 사례 — AIOS 라이브 트레이딩 코어에 그대로 채택 권장.
- "체결 직후에는 동기화하지 않는다"(최근 10초 내 fill 없을 때만)는 규칙은 현금/보유 반영과 브로커 폴링 사이의 레이스 컨디션을 피하는 핵심 안전장치.
- 미지의 포지션(구독하지 않은 종목)을 자동으로 universe에 편입하는 정책은 편의성이 크지만, AIOS에서는 "의도치 않은 포지션 인수"에 대한 명시적 알림/승인 플로우를 추가로 고려할 가치가 있음(LEAN은 조용히 흡수).

---

## 9. 기관형 실행을 위한 기타 주목할 요소

### 9.1 Time-in-Force — GTC/Day/GTD만 지원, IOC/FOK 부재

```csharp
// Common/Orders/TimeInForce.cs:28-61
public abstract class TimeInForce : ITimeInForceHandler
{
    public static readonly TimeInForce GoodTilCanceled = new GoodTilCanceledTimeInForce();
    public static readonly TimeInForce Day = new DayTimeInForce();
    public static Func<DateTime, TimeInForce> GoodTilDate => (expiry) => new GoodTilDateTimeInForce(expiry);
    public abstract bool IsOrderExpired(Security security, Order order);
    public abstract bool IsFillValid(Security security, Order order, OrderEvent fill);
}
```
코어 `Common/Orders/TimeInForces/`에는 `DayTimeInForce`, `GoodTilCanceledTimeInForce`, `GoodTilDateTimeInForce` 3종만 존재 — **IOC(Immediate-Or-Cancel)/FOK(Fill-Or-Kill)는 코어 레벨에 없음**(개별 브로커의 `*OrderProperties`에서 브로커 고유 확장으로 처리될 가능성은 있으나 `InteractiveBrokersOrderProperties.cs`, `OrderProperties.cs` 검색 결과 IOC/FOK 문자열 부재 확인). 기관형 알고리즘 실행(특히 dark pool/algo 주문)에서 IOC/FOK가 필수적임을 감안하면 이는 LEAN 코어의 명확한 갭.

### 9.2 Tag / OrderProperties — 브로커별 확장 프로퍼티

`Order.Tag`(자유 문자열, 사용자 메모/전략 식별용)와 `Order.Properties`(`IOrderProperties`)가 분리되어 있다. `Properties`는 `TimeInForce`를 담는 공통 베이스 외에 브로커별로 30개 이상의 서브클래스(`InteractiveBrokersOrderProperties`, `AlpacaOrderProperties`, `BinanceOrderProperties`, `BloombergFixOrderProperties`, `TradingTechnologiesOrderProperties` 등)가 존재 — 브로커 고유 필드(예: locate broker, allocation, algo strategy 파라미터)를 타입 안전하게 확장하는 패턴. `BrokerageExtensions.RemoveLocateFromNonShortOrder`(TransactionHandler가 SanitizeOrderProperties에서 호출)처럼, 주문 유형과 맞지 않는 브로커 프로퍼티는 제출 전 자동으로 제거된다.

### 9.3 Group/Combo Order — 원자적 다리(leg) 주문

`GroupOrderManager`(Common/Orders/GroupOrderManager.cs)가 `Id`, `Count`(leg 수), `Quantity`, `LimitPrice`, `OrderIds`(`HashSet<int>`, 스레드세이프 lock 필요 명시)를 보유하며 `ComboMarketOrder`/`ComboLimitOrder`/`ComboLegLimitOrder` 3종이 이를 참조한다. `BrokerageTransactionHandler.HandleSubmitOrderRequest`(897-934)에서 `order.TryGetGroupOrders`로 **그룹 내 모든 레그가 도착해야만** 브로커 제출을 진행하며(`comboIsReady` 체크), 하나라도 실패하면 `InvalidateOrders`로 그룹 전체를 무효화 — 콤보 주문의 all-or-nothing 원자성을 애플리케이션 레벨에서 보장.

### 9.4 Margin / Buying Power — `BuyingPowerModel` (Common/Securities/BuyingPowerModel.cs, 566 lines)

`HasSufficientBuyingPowerForOrder`(246행), `GetMaximumOrderQuantityForTargetBuyingPower`(366행), `GetReservedBuyingPowerForPosition`(549행), `GetInitialMarginRequirement`(230행)로 구성. `InitialMarginRequirement`가 위반되면 예외(`InvalidInitialMarginRequirement`, 68행). `SecurityMarginModel`(주식/일반 자산), `PatternDayTradingMarginModel`(PDT 규정 반영), `CashBuyingPowerModel`(현금 계좌, 레버리지 없음), `ConstantBuyingPowerModel`, `NullBuyingPowerModel` 등으로 계좌 유형별 세분화되어 있다. `BrokerageTransactionHandler.HasSufficientBuyingPowerForOrders`(1143-1183)가 제출/업데이트 양쪽에서 이를 호출해 사전 검증 게이트로 사용한다(2.2절 참고).

### 9.5 Settlement — T+N 현금 결제 모델

`ISettlementModel`(`ApplyFunds`/`Scan`/`GetUnsettledCash`)의 두 구현: `ImmediateSettlementModel`(즉시 결제, 기본), `DelayedSettlementModel`(T+N, Common/Securities/DelayedSettlementModel.cs):
```csharp
// Common/Securities/DelayedSettlementModel.cs:63-82
portfolio.UnsettledCashBook[currency].AddAmount(amount); // 매도 대금은 우선 미결제 장부로
var settlementDate = ...;
for (var i = 0; i < _numberOfDays; i++) { settlementDate = settlementDate.AddDays(1); if (!IsDateOpen) i--; } // 거래일 기준 T+N
_unsettledCashAmounts.Enqueue(new UnsettledCashAmount(settlementTimeUtc, currency, amount));
```
`Scan`(102-120)이 매 틱마다 큐를 확인해 결제일이 도래한 자금을 `UnsettledCashBook → CashBook`으로 이관 — 미국 주식 T+1/T+2 결제, 현금계좌의 free-riding 방지 등을 정밀하게 모델링.

### 9.6 OrderSizing — 실행 크기 산정 헬퍼

`Common/Orders/OrderSizing.cs`: `GetOrderSizeForPercentVolume`(바 거래량 대비 %), `GetOrderSizeForMaximumValue`(명목가 상한), 둘 다 `AdjustByLotSize`로 마무리해 lot size 정합성을 보장 — VWAP/StdDev execution model이 공통으로 재사용(5.3절).

**AIOS 시사점**
- IOC/FOK 부재는 AIOS가 LEAN 대비 명확히 우위를 점할 수 있는 지점 — 기관형 실행(특히 알고리즘 트레이딩, 다크풀 연계)에서는 TimeInForce 계층에 IOC/FOK/GTX 등을 1급 시민으로 설계해야 함.
- Tag(자유 텍스트) + Properties(타입 안전 확장) 이원 구조는 브로커 확장성과 사용자 메타데이터를 분리하는 좋은 패턴으로 채택 가치 있음.
- Combo order의 "전량 도착 후 원자적 제출, 실패 시 전체 무효화" 원칙은 AIOS의 멀티레그 전략(옵션 스프레드, 페어 트레이딩 등) 주문 처리에 필수 참고 모델.
- 계좌 유형(Cash/Margin/PDT)별로 `BuyingPowerModel`을 분리하고 Settlement를 별도 모델로 분리한 것은 규제 준수(PDT, 결제 지연)를 코드 레벨 정책 객체로 명시한 사례 — AIOS의 컴플라이언스 요구사항을 유사한 정책 객체 패턴으로 구현할 수 있음.

---

## 부록: 조사에 사용한 핵심 파일 목록

```
Common/Orders/Order.cs, OrderTypes.cs, OrderExtensions.cs, OrderTicket.cs,
  OrderRequest.cs, SubmitOrderRequest.cs, UpdateOrderRequest.cs, CancelOrderRequest.cs,
  OrderEvent.cs, OrderResponseErrorCode.cs, GroupOrderManager.cs, OrderSizing.cs, TimeInForce.cs
Common/Orders/Fees/{IFeeModel,OrderFee}.cs
Common/Orders/Fills/{FillModel,ImmediateFillModel,EquityFillModel}.cs
Common/Orders/Slippage/{ISlippageModel,VolumeShareSlippageModel}.cs
Common/Interfaces/IBrokerage.cs
Common/Brokerages/{IBrokerageModel,DefaultBrokerageModel,BrokerageMessageEvent}.cs
Common/Securities/{BuyingPowerModel,DelayedSettlementModel}.cs
Common/Statistics/{Statistics,PortfolioStatistics}.cs
Brokerages/Brokerage.cs
Algorithm/QCAlgorithm.Trading.cs
Algorithm/Execution/{ImmediateExecutionModel,IExecutionModel}.cs
Algorithm.Framework/Risk/MaximumDrawdownPercentPortfolio.cs
Algorithm.Framework/Execution/VolumeWeightedAveragePriceExecutionModel.cs
Engine/TransactionHandlers/BrokerageTransactionHandler.cs (2021 lines, 핵심)
Engine/RealTime/{IRealTimeHandler,BacktestingRealTimeHandler,LiveTradingRealTimeHandler}.cs
Engine/Results/BaseResultsHandler.cs
Engine/Setup/BrokerageSetupHandler.cs
```
