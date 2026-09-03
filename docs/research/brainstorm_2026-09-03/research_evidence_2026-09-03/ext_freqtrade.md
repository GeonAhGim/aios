# Freqtrade 코드 레벨 분석 — 엔터프라이즈 Trading-OS(AIOS) 설계 참고

- 대상: https://github.com/freqtrade/freqtrade (Python, `develop` 브랜치)
- 분석 스냅샷: commit `cab0ec50d67c3a5ea01674defa85d34bc766c295`, **2026-09-02 06:50 +0200** ("fix: don't overwrite an order's funding fee when the order is reprocessed"), 버전 문자열 `2026.9-dev` (`freqtrade/__init__.py:3`)
- 라이선스: **GPL-3.0** (`LICENSE`). **코드 복사/파생은 불가** — AIOS는 독자 코드베이스이므로 여기서는 *패턴·설계 결정*만 학습한다. 아래 verbatim 발췌는 분석 인용 목적이며 AIOS 코드로 옮겨 쓰면 안 된다.
- 규모: `freqtrade/exchange/exchange.py` 4,230줄, `freqtradebot.py` 2,698줄, `strategy/interface.py` 1,910줄, `optimize/backtesting.py` 1,978줄, 테스트 파일 140개.
- 모든 경로는 clone root `scratchpad/ext/freqtrade/` 기준.

---

## 1. Exchange adapter layer (ccxt wrapper)

### 1.1 Retry / backoff — `freqtrade/exchange/common.py`

동기(`retrier`)·비동기(`retrier_async`) 두 데코레이터가 있고, 재시도 대상 예외는 `TemporaryError`(및 서브클래스 `DDosProtection`)와 `RetryableOrderError`뿐이다. 즉 "재시도해도 되는가"를 **예외 타입으로 인코딩**한다. 재시도 횟수는 kwargs `count`로 재귀 전달된다.

```python
# common.py:33-36
API_RETRY_COUNT = 4
API_FETCH_ORDER_RETRY_COUNT = 5
# common.py:114-121
def calculate_backoff(remaining_retries, max_retries):
    return (max_retries - remaining_retries) ** 2 + 1
# common.py:180-198 (retrier wrapper 본문)
            except (TemporaryError, RetryableOrderError) as ex:
                if count > 0:
                    count -= 1
                    kwargs.update({"count": count})
                    if isinstance(ex, DDosProtection | RetryableOrderError):
                        backoff_delay = calculate_backoff(count + 1, retries)
                        time.sleep(backoff_delay)
                    return wrapper(*args, **kwargs)
                else:
                    raise ex
```

- 백오프는 quadratic(1, 2, 5, 10, 17s …)이며 jitter 없음. DDoS/OrderNotFound일 때만 sleep, 그 외 TemporaryError는 즉시 재시도.
- `fetch_order`는 `@retrier(retries=API_FETCH_ORDER_RETRY_COUNT)`(5회), `create_stoploss`는 `@retrier(retries=0)`(주문 중복 생성 방지 목적으로 재시도 금지 — `exchange.py:1580`).
- `common.py:137-146`에 KuCoin `429000` 전용 workaround가 하드코딩되어 있음(거래소 quirk가 공용 재시도 로직에 새어 들어온 예).
- `BAD_EXCHANGES`(`common.py:38-45`)로 지원 불가 거래소를 사유와 함께 명시적으로 차단.

### 1.2 Rate limiting

자체 rate limiter는 없다. ccxt의 `enableRateLimit`(기본 true)에 위임하며, 설정 `exchange.ccxt_config / ccxt_async_config`로 `rateLimit`(ms)를 조정하도록 문서화(`docs/exchanges.md:38-53`). `_init_ccxt`(`exchange.py:381-433`)에서 `_ccxt_params`(클래스 정적, 예: okx `brokerId`) → 사용자 `ccxt_kwargs` 순으로 deep-merge 후 ccxt 인스턴스를 만든다.

### 1.3 Error taxonomy — `freqtrade/exceptions.py`

```
FreqtradeException
├─ OperationalException      # "Requires manual intervention and will stop the bot."
│  └─ ConfigurationError
├─ DependencyException       # "assumed dependency is not met" (잔고 부족 등)
│  ├─ PricingError
│  └─ ExchangeError
│     ├─ InvalidOrderException
│     │  ├─ RetryableOrderError   # "order is not found ... repeated with increasing backoff"
│     │  └─ InsufficientFundsError
│     └─ TemporaryError           # "Temporary network or exchange related error"
│        └─ DDosProtection        # "Bot will wait for a second and then retry."
└─ StrategyError                 # "Errors with custom user-code detected."
```

ccxt 예외 → 자체 예외 매핑은 각 API 메서드에 반복적으로 인라인되어 있다(`create_order`, `exchange.py:1508-1527`):

```python
        except ccxt.InsufficientFunds as e:
            raise InsufficientFundsError(...) from e
        except ccxt.InvalidOrder as e:
            raise InvalidOrderException(...) from e
        except ccxt.DDoSProtection as e:
            raise DDosProtection(e) from e
        except (ccxt.OperationFailed, ccxt.ExchangeError) as e:
            raise TemporaryError(...) from e
        except ccxt.BaseError as e:
            raise OperationalException(e) from e
```

핵심 의미론: **`OperationalException`은 봇을 STOPPED로 전이**시키고(`worker.py:212-224`), `TemporaryError`는 `RETRY_TIMEOUT=30s` 후 루프 재시도, `DependencyException` 계열은 해당 trade 처리만 건너뛴다.

### 1.4 거래소 quirk 격리 — `_ft_has` dict + 서브클래스

`Exchange._ft_has_default`(`exchange.py:131-173`)에 ~40개의 capability flag가 있고, 서브클래스(`binance.py`, `bybit.py`, `okx.py`, `kraken.py`, `gate.py`, `hyperliquid.py` 등 27개 파일)가 `_ft_has` / `_ft_has_futures`로 덮어쓴다. 사용자 config `exchange._ft_has_params`로도 override 가능.

```python
# exchange.py:131-147 (발췌)
    _ft_has_default: FtHas = {
        "stoploss_on_exchange": False,
        "stop_price_param": "stopLossPrice",  # Used for stoploss_on_exchange request
        "stop_price_prop": "stopLossPrice",  # Used for stoploss_on_exchange response parsing
        "stoploss_order_types": {},
        "stoploss_blocks_assets": True,  # By default stoploss orders block assets
        "order_time_in_force": ["GTC"],
        "ohlcv_has_history": True,  # Some exchanges (Kraken) don't provide history via ohlcv
        "ohlcv_partial_candle": True,
        "always_require_api_keys": False,  # purge API keys for Dry-run. Must default to false.
        "trades_pagination": "time",  # Possible are "time" or "id"
        "marketOrderRequiresPrice": False,
        "exchange_has_overrides": {},  # Dictionary overriding ccxt's "has".
        "ws_enabled": False,  # Set to true for exchanges with tested websocket support
```

병합 순서(`exchange.py:967-990`): `_ft_has_default` ← `_ft_has` ← (futures면) `_ft_has_futures` ← config `_ft_has_params`. Binance futures 예(`binance.py:54-78`): `stoploss_order_types: {"limit":"stop","market":"stop_market"}`, `fetch_orders_limit_minutes: 7*1440`, `balance_includes_unrealized_pnl: True`, `proxy_coin_mapping: {"BNFCR":"USDC"}`. `exchange_has()`(`exchange.py:1008-1017`)는 ccxt `has`를 `exchange_has_overrides`로 덮어써서 ccxt의 잘못된 capability 선언을 교정한다.

### 1.5 Order fetch / cancel 의미론, "order not found"

- `fetch_order`(`exchange.py:1716-1745`): `ccxt.OrderNotFound` → `RetryableOrderError`(backoff 재시도 5회). 거래소가 `fetchOrder`를 지원하지 않으면 `fetch_order_emulated`(open/closed 목록 스캔)로 폴백.
- `cancel_order_with_result`(`exchange.py:1832-1861`): cancel 응답이 부실하면(`is_cancel_order_result_suitable` — `fee/status/amount` 필수) `fetch_order`로 재조회, 그것도 실패하면 **합성 canceled 주문 dict**를 만들어 상위 로직이 진행되게 한다.
- `check_order_canceled_empty`(`exchange.py:1782-1788`): `status in NON_OPEN_EXCHANGE_STATES and filled == 0.0` → "빈 취소" 판정. 부분체결 취소는 별도 경로.
- `create_order`(`exchange.py:1489-1495`): 거래소가 `status=None`이거나 market 주문의 `average`가 없으면 강제로 `"open"`으로 매핑해 다음 루프에서 재조회.

### 1.6 Precision / amount rounding

`amount_to_precision`·`price_to_precision`(`exchange.py:1053-1071`)이 ccxt `precisionMode`(DECIMAL_PLACES/TICK_SIZE 등)를 존중해 반올림. 가격은 `rounding_mode` 파라미터를 받으며 docstring에 "For stoploss calculations, must use ROUND_UP for longs, and ROUND_DOWN for shorts." 계약 크기(`_amount_to_contracts` / `_contracts_to_amount`, `exchange.py:666-672`)를 선물 거래소에 대해 변환하고, `order_props_in_contracts`로 어떤 응답 필드가 계약 단위인지 선언한다. `startup_backpopulate_precision`(`freqtradebot.py:417`)이 DB의 과거 trade에 precision을 소급 기록.

### 1.7 Fee 추출

- `order_has_fee`(`exchange.py:2516-2531`): `fee.currency`·`fee.cost` 모두 non-null일 때만 신뢰.
- `calculate_fee_rate`(`exchange.py:2533-2572`): rate가 없으면 base/quote/제3통화 세 경우로 나눠 계산, 제3통화는 `get_conversion_rate`로 환산하고 실패 시 config `unknown_fee_rate` 폴백.
- 봇 레벨 `get_real_amount`(`freqtradebot.py:2549-2593`): "Reject all fees that report as > 2%. These are most likely caused by a parsing bug in ccxt" — **2% 초과 수수료는 ccxt 버그로 간주해 거부**하고, 주문 dict에 fee가 없으면 `fee_detection_from_trades`로 `fetch_my_trades` 결과를 order_id로 필터해 합산(`get_trades_for_order`, `exchange.py:2408-2455`, since -5s 오프셋).

### AIOS 시사점
- 재시도 가능 여부를 **예외 타입 계층으로 표현**하고 데코레이터가 타입만 보고 결정하는 패턴은 그대로 채택할 가치가 있다. 단 AIOS는 jitter 있는 exponential backoff + per-endpoint budget + circuit breaker를 추가해야 한다(Freqtrade는 quadratic, jitter 없음, 전역 상태 없음).
- 거래소 quirk를 `capability flag dict + 얕은 서브클래스`로 격리하는 방식은 확장성이 좋았다. AIOS는 이를 **선언적 capability manifest**(버전 관리·테스트 가능한 데이터)로 승격하고, KuCoin 429처럼 공용 코드에 새어 들어온 특수 처리를 금지하는 린트 규칙을 두는 게 좋다.
- "order not found → 재시도 후 합성 canceled 응답" 같은 **불확실한 거래소 응답을 결정론적 상태로 수렴시키는 폴백 사다리**는 필수. AIOS에서는 각 폴백 단계가 감사 로그에 남아야 한다.
- 수수료 sanity check(>2% 거부)·제3통화 환산 폴백은 실전에서 나온 규칙 — AIOS의 fee/PnL 엔진에 동일한 검증 게이트를 두되 임계값을 설정화한다.

---

## 2. Dry-run vs Live 스위치

### 2.1 스위치 위치와 강제 지점

단일 boolean `config["dry_run"]`이 **거래소 어댑터의 메서드 입구**에서 분기된다. 상위(봇/RPC)는 dry-run 여부를 거의 모른 채 동일한 코드 경로를 탄다.

```python
# exchange.py:1466-1470 (create_order)
        if self._config["dry_run"]:
            dry_order = self.create_dry_run_order(
                pair, ordertype, side, amount, self.price_to_precision(pair, rate), leverage
            )
            return dry_order
# exchange.py:1718-1719 (fetch_order)
        if self._config["dry_run"]:
            return self.fetch_dry_run_order(order_id)
# exchange.py:1792-1798 (cancel_order)
        if self._config["dry_run"]:
            try:
                order = self.fetch_dry_run_order(order_id)
                order.update({"status": "canceled", "filled": 0.0, "remaining": order["amount"]})
```

`get_trades_for_order`(`exchange.py:2428`)도 dry-run이면 `[]`. `freqtradebot.py`에서는 `startup_update_open_orders`(434)·`update_trades_without_assigned_fees`(481)만 dry-run에서 skip.

### 2.2 실주문 방지 3중 장치

1. **API key 제거**: `Exchange.__init__`(`exchange.py:260-263`)이 `remove_exchange_credentials(exchange_conf, not always_require_api_keys and dry_run)`을 호출 → `configuration/config_secrets.py:52-66`이 `_SENSITIVE_KEYS`(`exchange.key/secret/password/uid/private_key…`)를 config dict에서 **삭제**하므로 dry-run 프로세스는 서명된 요청을 보낼 수단 자체가 없다.
2. **DB 분리**: `configuration.py:148-156` — dry-run이면 `db_url`이 prod 기본값일 때 `tradesv3.dryrun.sqlite`로 강제 교체.
3. **RunMode enum**: `enums/runmode.py` `LIVE / DRY_RUN / BACKTEST / HYPEROPT / WEBSERVER…`, `TRADE_MODES = [LIVE, DRY_RUN]`. `Wallets.update`(`wallets.py:259`)는 `not dry_run or runmode == LIVE`일 때만 실제 잔고 조회.

### 2.3 Dry-run 주문 체결 시뮬레이션

`create_dry_run_order`(`exchange.py:1161-1239`)는 실제 L2 orderbook을 조회해 체결을 판정한다.

```python
        order_id = f"dry_run_{side}_{pair}_{uuid4()}"
        ...
        if not stop_loss and ordertype == "limit" and orderbook:
            # Allow a 1% price difference
            allowed_diff = 0.01
            if self._dry_is_price_crossed(pair, side, rate, orderbook, allowed_diff):
                dry_order["type"] = "market"
        if dry_order["type"] == "market" and not dry_order.get("ft_order_type"):
            # Update market order pricing
            slippage = 0.05
            worst_rate = rate * ((1 + slippage) if side == "buy" else (1 - slippage))
            average = self.get_dry_market_fill_price(pair, side, amount, rate, worst_rate, orderbook)
            ...
            # market orders will always incurr taker fees
            dry_order = self.add_dry_order_fee(pair, dry_order, "taker")
```

- 시장가는 orderbook을 걸어 내려가며 VWAP 체결(최악 5% 슬리피지 상한), limit는 spread를 1% 이상 넘으면 market으로 강등, 체결되면 maker/taker 수수료 구분 부여.
- 미체결 주문은 `_dry_run_open_orders` dict(`exchange.py:253`)에 보관되고, `fetch_dry_run_order`(`1401-1424`)가 호출될 때마다 `check_dry_limit_order_filled`로 현재 orderbook과 비교해 lazy하게 체결. 프로세스 재시작 후 dict가 비면 DB `Order.order_by_id`로 복원(`1410-1416`).
- Stoploss dry 주문은 `ft_order_type="stoploss"` 태그로 즉시 체결을 막고, 생성 시점에 이미 트리거되면 `InvalidOrderException("Stoploss would trigger immediately.")`.

### 2.4 Wallet 시뮬레이션

`Wallets._update_dry`(`wallets.py:107-186`): `dry_run_wallet` 시작 자본 + 종료된 trade의 누적 수익(`Trade.get_total_closed_profit`) − 오픈 trade에 묶인 stake로 잔고를 **DB로부터 재구성**한다. 즉 dry-run 잔고의 single source of truth는 trade DB.

### AIOS 시사점
- 어댑터 경계에서 분기하고 **credential을 물리적으로 제거**하는 이중 안전장치는 매우 강력하다. AIOS는 한 걸음 더 나아가 dry-run 프로세스에 *read-only API key만* 주입하는 secret scope 분리와, 라이브 주문 함수가 `ExecutionMode.LIVE` capability token 없이는 호출조차 안 되는 타입 레벨 강제를 권장.
- Freqtrade의 dry-run은 실제 orderbook 기반 fill 모델이라 "paper trading의 현실성"이 높다. AIOS의 paper 엔진도 최소 이 수준(L2 walk + maker/taker 구분 + partial fill lazy 판정)을 갖춰야 백테스트-페이퍼-라이브 간 괴리가 줄어든다.
- dry-run 상태(open orders dict)가 in-memory라 재시작 시 DB 폴백에 의존한다. AIOS는 paper 주문도 동일한 order store에 기록해 라이브와 완전히 같은 복구 경로를 타게 해야 한다.

---

## 3. Persistence & Recovery

### 3.1 모델 — `freqtrade/persistence/trade_model.py`

- `Order`(65-383): `order_id`(index), `ft_trade_id`(FK), `ft_order_side`(buy/sell/stoploss), `ft_is_open`(index), `status`, `filled/remaining/average/cost`, `stop_price`, `funding_fee`, `ft_fee_base`, `ft_cancel_reason`, `order_update_date`. 거래소 응답을 `update_from_ccxt_object`(197-229)로 *부분 갱신*(`safe_value_fallback`으로 None 필드는 기존값 유지, id 불일치 시 `DependencyException`).
- `LocalTrade`(385, `use_db=False`) ↔ `Trade(ModelBase, LocalTrade)`(1706, `use_db=True`): 백테스트는 같은 도메인 로직을 DB 없이 in-memory로 돌린다. `orders` relationship은 `lazy="selectin", cascade="all, delete-orphan"`.
- `models.py:init_db`: `scoped_session(..., scopefunc=get_request_or_thread_id)`로 FastAPI 요청 컨텍스트 / 스레드별 세션 분리. SQLite는 `check_same_thread=False`, 마이그레이션은 `migrations.py:check_migrate`가 컬럼 존재 여부로 판단하는 **자체 마이그레이션**(Alembic 미사용), `record_version` 컬럼으로 행 단위 재계산 이력 관리(`migrations.py:393-410`). `set_sqlite_to_wal`(351).
- 부가 테이블: `PairLock`, `_KeyValueStoreModel`(startup time 등), `_CustomData`(strategy가 trade에 붙이는 KV), `WalletHistory`(일 1회 잔고 스냅샷, `freqtradebot.py:183`).

### 3.2 재시작 시 복구 — `FreqtradeBot.startup` / `startup_update_open_orders`

```python
# freqtradebot.py:242-260
    def startup(self) -> None:
        migrate_live_content(self.config, self.exchange, self.wallets.get_starting_balance())
        set_startup_time()
        self.startup_backpopulate_precision()
        Trade.stoploss_reinitialization(self.strategy.stoploss)
        self.startup_update_open_orders()
        self.update_all_liquidation_prices()
        self.update_funding_fees()
```

```python
# freqtradebot.py:429-474 (발췌)
        orders = Order.get_open_orders()
        for order in orders:
            try:
                fo = self.exchange.fetch_order_or_stoploss_order(
                    order.order_id, order.ft_pair, order.ft_order_side == "stoploss")
                if not order.trade:
                    logger.warning(f"Order {order.order_id} has no trade attached. "
                        "This may suggest a database corruption. ...")
                    continue
                self.update_trade_state(order.trade, order.order_id, fo,
                    stoploss_order=(order.ft_order_side == "stoploss"))
            except InvalidOrderException as e:
                if order.order_date_utc - timedelta(days=5) < datetime.now(UTC):
                    logger.warning("Order is older than 5 days. Assuming order was fully cancelled.")
                    fo = order.to_ccxt_object(); fo["status"] = "canceled"
                    self.handle_cancel_order(fo, order, order.trade, constants.CANCEL_REASON["TIMEOUT"])
```

즉 reconciliation의 원천은 **DB의 open Order 목록**이며, 거래소를 pull해서 DB 상태를 갱신하는 방향(DB→exchange). 거래소에만 있고 DB에 없는 주문은 `handle_onexchange_order`(`543-651`)가 `fetch_orders(pair, since=open_date-10s)`로 발견해 `ExitType.SOLD_ON_EXCHANGE`로 붙인다 — 단 잔고 부족(`InsufficientFundsError`)이 감지됐을 때만 호출되는 사후 경로다.

### 3.3 매 루프의 주문 상태 갱신

- `process()`(`284-338`) 순서: `reload_markets` → `update_trades_without_assigned_fees` → `Trade.get_open_trades` → `dataprovider.refresh` → `strategy.analyze` → **`manage_open_orders`**(with `_exit_lock`) → `exit_positions` → `process_open_trade_positions` → `enter_positions` → `Trade.commit()`.
- `manage_open_orders`(`1629-1660`): 모든 open order를 `fetch_order` → `update_trade_state` → 여전히 open이면 `strategy.ft_check_timed_out`(unfilledtimeout) 시 `handle_cancel_order`, 아니면 `replace_order`(strategy `adjust_entry_price`).
- `handle_cancel_enter`(`1905-1994`): 부분체결 stake가 min-stake 미만이면 취소 거부("would result in an unexitable trade"), cancel 후 `status not in NON_OPEN_EXCHANGE_STATES`면 **race condition으로 보고 bail**("this order will then be handled in the next iteration").
- `handle_cancel_order`(`1662-1687`): exit 주문이 `exit_timeout_count`회 타임아웃되면 `emergency_exit`(시장가 강제 청산).
- `update_trade_state`(`2366-2418`): 단일 진입점 — `trade.update_order` → `check_order_canceled_empty` → `handle_order_fee` → `trade.update_trade` → `_update_trade_after_fill` → `Trade.commit()` → 알림.
- 종료된 trade의 수수료 누락은 `update_trades_without_assigned_fees`(`476-515`)가 매 루프 보정.

### 3.4 Stoploss on exchange

`handle_stoploss_on_exchange`(`1494-1548`): open SL order를 `fetch_stoploss_order`로 조회 → `closed/triggered`면 `ExitType.STOPLOSS_ON_EXCHANGE`로 종료 + `handle_protections`; 포지션이 있는데 SL이 없으면 `create_stoploss_order`; `stoploss_blocks_assets`(거래소별 flag)가 True이고 다른 open order가 있으면 SL 생성을 미룬다. Trailing은 `handle_trailing_stoploss_on_exchange`(`1591`)가 cancel→재생성. `create_stoploss`는 `@retrier(retries=0)` — 중복 SL 방지. `Trade.stoploss_reinitialization`(`trade_model.py:1586-1603`)은 재시작 시 strategy stoploss가 바뀌면 오픈 trade의 initial SL을 재설정(trailing 중이면 skip).

### AIOS 시사점
- "DB의 open orders가 진실, 거래소는 pull하여 수렴" 모델은 단순하고 견고하지만 **거래소에만 존재하는 주문/포지션**의 발견이 사후적(잔고 부족 시)이다. AIOS는 startup과 주기적으로 양방향(full position/open-order snapshot diff) reconciliation을 돌리고, 불일치를 alert 큐로 보내야 한다.
- `update_trade_state`처럼 **주문 상태 변화의 단일 진입점**을 두는 것은 필수. AIOS는 이를 이벤트 소싱(OrderEvent append-only)으로 만들고 현재 상태는 projection으로 유도하면 감사·재생(replay)이 공짜로 따라온다.
- SQLAlchemy 자체 마이그레이션 + SQLite WAL은 단일 봇엔 충분하나 다중 인스턴스엔 부적합. AIOS는 Postgres + Alembic/버전드 스키마, `record_version`류의 행 단위 재계산 버전은 좋은 아이디어이니 유지.
- 부분체결 + 취소 race를 "다음 루프에 맡긴다"는 결정은 idempotent 루프 설계의 전형. AIOS도 매 tick이 idempotent하도록 설계하되, 최대 대기 tick 수와 escalation을 명시해야 한다.

---

## 4. Strategy interface — `freqtrade/strategy/interface.py`

### 4.1 Lifecycle hooks (IStrategy, `INTERFACE_VERSION = 3`, line 68)

| 단계 | Hook (line) | 비고 |
|---|---|---|
| 초기화 | `bot_start`(282), `ft_bot_start`(217), `bot_loop_start`(288) | 매 루프 시작 콜백 |
| 신호 | `populate_indicators`(236) / `populate_entry_trend`(254) / `populate_exit_trend`(273) | DataFrame 벡터 연산, `analyze_pair`→`_analyze_ticker_internal`(1206) |
| 진입 게이트 | `confirm_trade_entry`(359), `custom_stake_amount`(625), `custom_entry_price`(506), `leverage`(832) | 주문 직전 |
| 포지션 관리 | `custom_stoploss`(446), `custom_roi`(477), `custom_exit`(594), `adjust_trade_position`(654), `adjust_entry_price`/`adjust_exit_price`(697/734) | trade별 매 루프 |
| 퇴출 게이트 | `confirm_trade_exit`(395), `custom_exit_price`(534) | |
| 주문 이벤트 | `check_entry_timeout`/`check_exit_timeout`(305/336), `order_filled`(433) | |
| 메타 | `informative_pairs`(857), `version`(870), `protections`(class attr, 80), `plot_config` | |

`custom_stoploss` docstring: "The custom stoploss can never be below self.stoploss, which serves as a hard maximum loss." — 전략 코드가 하드 리스크 한도를 넘지 못하게 프레임워크가 clamp한다(`ft_stoploss_adjust`, 1524). `confirm_trade_entry`는 "When not implemented by a strategy, returns True"이며 "Timing for this function is critical".

### 4.2 로딩·해결 — `resolvers/strategy_resolver.py`, `iresolver.py`

`IResolver._get_valid_object`(`iresolver.py:93-99`)가 `importlib.util.spec_from_file_location` → `exec_module`로 `user_data/strategies/*.py`를 **그대로 import**한다. `_load_strategy`(`strategy_resolver.py:257-310`)는 `"Name:<base64>"` 형태로 **base64 인코딩된 소스를 임시 디렉터리에 써서 로드**하는 경로도 지원한다(원격 배포용).

```python
# strategy_resolver.py:281-292
        if ":" in strategy_name:
            logger.info("loading base64 encoded strategy")
            strat = strategy_name.split(":")
            if len(strat) == 2:
                temp = Path(tempfile.mkdtemp("freq", "strategy"))
                temp.joinpath(name).write_text(urlsafe_b64decode(strat[1]).decode("utf-8"))
```

`validate_strategy`(176-255)는 `populate_entry_trend/exit_trend` 구현 여부, 구식 `buy/sell` 이름 혼용, `INTERFACE_VERSION`별 호환성 검사만 수행. `_normalize_attributes`(130)가 config로 strategy 속성을 override.

### 4.3 버전

`IStrategy.version()`(870-874)은 기본 `None`을 반환하는 **선택적 문자열**. `worker.py:134-141` heartbeat 로그에 `strategy_version`으로 찍힌다. 그 외 strategy 해시·서명·소스 pinning은 없음(단 `__source__`/`__file__` 속성에 소스 텍스트 보관, `interface.py:90-91`).

### 4.4 샌드박싱 — 사실상 없음

- 격리·리소스 제한·권한 분리 없음. strategy는 봇 프로세스 안에서 `self.dp`(DataProvider), `self.wallets`, config 전체에 접근(`freqtradebot.py:130-133`).
- 유일한 방어는 `strategy_safe_wrapper`(`strategy/strategy_wrapper.py:31-62`):

```python
def strategy_safe_wrapper(f: F, message: str = "", default_retval=None, supress_error=False) -> F:
    """ Caches all exceptions and returns either the default_retval (if it's not None) or raises
    a StrategyError exception, which then needs to be handled by the calling method. """
    def wrapper(*args, **kwargs):
        try:
            if not (getattr(f, "__qualname__", "")).startswith("IStrategy."):
                if "trade" in kwargs:
                    # Protect accidental modifications from within the strategy
                    kwargs["trade"] = deepcopy(kwargs["trade"])
            return f(*args, **kwargs)
        except ValueError as error: ... raise StrategyError(str(error)) from error
        except Exception as error: ... raise StrategyError(str(error)) from error
```

즉 (a) 예외를 `StrategyError`로 감싸고 default 반환, (b) `trade` 객체를 deepcopy해 전략의 우발적 변조를 막는다. 실행 시간은 `MeasureTime`(`freqtradebot.py:186-197`)이 timeframe의 25%를 넘으면 **경고만** 한다.

### AIOS 시사점
- Hook 분류(신호 생성 / 진입·퇴출 게이트 / 포지션 관리 / 주문 이벤트)와 "프레임워크가 전략의 리스크 값을 clamp한다"는 원칙은 그대로 채택. AIOS는 hook마다 **타임아웃·호출 예산·허용 side effect**를 선언하게 해야 한다.
- 전략을 봇 프로세스에서 exec하는 모델은 기관용으로 부적합. AIOS는 최소 별도 프로세스/컨테이너 + gRPC/IPC 경계, 이상적으로는 전략이 *신호·의도(intent)*만 반환하고 주문 권한은 OMS가 갖는 구조가 필요하다. base64 소스 로딩 경로는 공급망 리스크의 예로 기억할 것.
- `version()`이 선택적 문자열이라 재현성이 약하다. AIOS는 전략 아티팩트 해시 + 파라미터 스냅샷 + 데이터 스냅샷 id를 모든 주문에 태깅해야 한다.
- `trade` deepcopy 전달은 값비싸지만 안전한 기본값 — AIOS는 immutable view 타입으로 같은 효과를 싸게 낼 수 있다.

---

## 5. Hyperopt & Backtesting, Protections

### 5.1 Backtesting engine — `optimize/backtesting.py`

- 시간 루프: `time_pair_generator`(1640) → `backtest_loop`(1520-1580, "This method is used by Hyperopt at each iteration. Please keep it optimized."). 캔들당 순서: (1) 오픈 주문 관리/타임아웃 → (2) 진입(`PairLocks.is_pair_locked`·`trade_slot_available` 검사, 거부는 `_collate_rejected`) → (3) 진입 주문 체결 → (4) 퇴출 주문 생성 → (5) 퇴출 주문 체결.
- DataFrame을 tuple list로 변환해 순회(`_get_ohlcv_as_lists`, 518) — 성능 최적화.
- **수수료**: `set_fee`(268-281) — config `fee` 없으면 `exchange.get_fee`로 **최악(가장 높은) taker fee** 채택: "worst case fee from exchange (lowest tier)".
- **슬리피지**: 전용 모델은 없다. limit 진입가는 `custom_entry_price` 적용 후 캔들 high/low로 clamp(`get_valid_entry_price_and_stake`, 1024-1060: "We can't place orders higher than current high"), 시장가는 캔들 open 체결. `backtest_detail`(timeframe-detail, 288/1582)로 하위 타임프레임을 써서 체결 정밀도 향상. 펀딩비는 `_run_funding_fees`(1007)로 8h 간격 반영.
- 백테스트는 `LocalTrade`(`use_db=False`) + `Wallets(is_backtest=True)` + `PairLocks`/`ProtectionManager`를 그대로 재사용 → 라이브 도메인 로직과 동일 코드.

### 5.2 Lookahead / Recursive bias 도구 — `optimize/analysis/`

- `LookaheadAnalysis.analyze_indicators`(`lookahead.py:69-96`): 전체 구간으로 계산한 indicator DataFrame과 **진입/퇴출 시점에서 잘라낸 구간**으로 재계산한 DataFrame을 `DataFrame.compare`로 비교, 값이 다르면 "found look ahead bias in column …"으로 보고. `docs/lookahead-analysis.md`의 `freqtrade lookahead-analysis` 커맨드.
- `RecursiveAnalysis.analyze_indicators`(`recursive.py:47-91`): startup_candle_count를 달리해 마지막 행 indicator 값을 비교, 상대 차이(`diff = (other-self)/self`)를 표로 출력 → EMA 등 재귀 지표의 warm-up 부족 탐지.

### 5.3 Hyperopt loss

`IHyperOptLoss.hyperopt_loss_function(*, results, trade_count, min_date, max_date, config, processed, backtest_stats, starting_balance)`(`hyperopt_loss_interface.py`) — "returns smaller number for better results". 구현 13종: Sharpe/Sortino(+daily), Calmar, MaxDrawdown(+relative, per-pair), ProfitDrawdown, OnlyProfit, ShortTradeDur, MultiMetric. 예: `hyperopt_loss_sharpe.py:37-38` `return -sharp_ratio`.

### 5.4 Runtime Protections — `plugins/protections/`

`IProtection`(`iprotection.py`): `has_global_stop` / `has_local_stop`, `global_stop()` / `stop_per_pair()`가 `ProtectionReturn(lock, until, reason, lock_side)`를 반환하고 `PairLocks`(DB `pairlocks` 테이블)로 잠근다. 봇은 trade 종료마다 `handle_protections`(`freqtradebot.py:2482-2504`)에서 평가하며, 항상 "Auto lock"으로 **한 캔들 재진입 금지**를 먼저 건다.

| Protection | 파일 | 규칙 |
|---|---|---|
| StoplossGuard | `stoploss_guard.py:44-84` | lookback 내 `exit_reason ∈ {stop_loss, trailing_stop_loss, stoploss_on_exchange, liquidation}` 이고 `close_profit < required_profit`인 trade가 `trade_limit`개 이상이면 pair 또는 전체 잠금 |
| MaxDrawdown | `max_drawdown_protection.py:46-111` | lookback 내 `calculate_max_drawdown` > `max_allowed_drawdown` 시 전체 잠금 |
| CooldownPeriod | `cooldown_period.py:28-53` | 해당 pair 마지막 종료 후 `stop_duration` 동안 재진입 금지 |
| LowProfitPairs | `low_profit_pairs.py:42-82` | lookback 내 `trade_limit`개 이상 거래의 합산 수익 < `required_profit`이면 pair 잠금 |

```python
# stoploss_guard.py:78-84
        return ProtectionReturn(
            lock=True,
            until=until,
            reason=self._reason(),
            lock_side=(side if self._only_per_side else "*"),
        )
```

잠금 해제 시각은 `calculate_lock_end`(`iprotection.py:125-143`) — `stop_duration` 또는 고정 시각 `unlock_at("HH:MM")`.

### AIOS 시사점
- 백테스트·페이퍼·라이브가 **같은 Trade/Wallet/Protection 도메인 코드**를 공유하는 구조(`LocalTrade` vs `Trade`)가 Freqtrade 신뢰성의 근간. AIOS도 "실행 환경만 바뀌고 도메인 로직은 하나"를 설계 원칙으로.
- lookahead-analysis(구간 절단 후 재계산 비교)와 recursive-analysis(warm-up 민감도)는 **CI 게이트로 자동화**할 가치가 있다 — 전략 배포 파이프라인에 필수 체크로 넣을 것.
- Protections는 사후(trade 종료 후) 평가되는 *entry gate*이며 실행 중 손실 한도(intraday P&L kill switch), 노출/레버리지 한도, 주문 빈도 한도는 없다. AIOS의 pre-trade risk 레이어는 이를 보완하되, `ProtectionReturn(lock, until, reason, side)` 같은 **설명 가능한 잠금 객체**와 DB 영속 잠금 패턴은 차용.
- 백테스트 슬리피지 모델 부재는 명확한 한계 — AIOS는 orderbook depth 기반 impact model을 백테스트/페이퍼 양쪽에서 동일하게 써야 한다.

---

## 6. Bot loop & process model, RPC

### 6.1 Worker와 상태 기계 — `worker.py`, `enums/state.py`

`State = RUNNING(1) / PAUSED(2) / STOPPED(3) / RELOAD_CONFIG(4)`. `Worker.run`은 무한 루프로 `_worker`를 호출하고 `RELOAD_CONFIG`면 `_reconfigure`(cleanup → config 재로드 → `FreqtradeBot` 재생성).

```python
# worker.py:96-108 (상태 전이)
        if state != old_state:
            if old_state != State.RELOAD_CONFIG:
                self.freqtrade.notify_status(f"{state.name.lower()}")
            if state in (State.RUNNING, State.PAUSED) and old_state not in (State.RUNNING, State.PAUSED):
                self.freqtrade.startup()
            if state == State.STOPPED:
                self.freqtrade.check_for_open_trades()
# worker.py:206-224 (예외 → 상태)
        except TemporaryError as error:
            logger.warning(f"Error: {error}, retrying in {RETRY_TIMEOUT} seconds...")
            time.sleep(RETRY_TIMEOUT)
        except OperationalException:
            hint = "Issue `/start` if you think it is safe to restart."
            self.freqtrade.notify_status(f"*OperationalException:*\n```\n{tb}```\n {hint}", ...)
            self.freqtrade.state = State.STOPPED
```

- 초기 상태는 config `initial_state`, 기본 **STOPPED**(`freqtradebot.py:145-146`) → 시작 직후 자동 거래 안 함.
- PAUSED: `process()`에서 `enter_positions`만 `state == RUNNING` 조건으로 막고(`freqtradebot.py:333`) 오픈 포지션 관리는 계속 — 우아한 wind-down.
- STOPPED에서 `cancel_open_orders_on_exit` 설정 시 미체결 주문 전부 취소(`process_stopped`, 340).
- systemd 연동: `sd_notify`로 `READY/WATCHDOG/RELOADING/STOPPING`(`worker.py:41,111,123,197,235`), `freqtrade.service.watchdog` 유닛 파일 제공.

### 6.2 Throttling

`_throttle`(`worker.py:147-192`): `process_throttle_secs`(기본 `PROCESS_THROTTLE_SECS=5`) 보장 + 다음 캔들 경계(`timeframe_to_next_date`)+1s 오프셋까지로 sleep 상한. 단일 스레드 동기 루프이며, RPC(Telegram/REST)는 별도 스레드에서 `_exit_lock`(threading.Lock, `freqtradebot.py:149`)으로 force-exit와 루프의 충돌만 막는다(`process()` 주석: "telegram messages arrive in an different thread"). `_measure_execution`이 전략 분석이 timeframe의 25% 초과 시 경고.

### 6.3 RPC layer & 명령 권한

- `RPC` 클래스(`rpc/rpc.py`)가 도메인 명령을 소유: `_rpc_start/_rpc_stop/_rpc_pause/_rpc_reload_config`(970-1009)는 단순히 `self._freqtrade.state = State.X` 대입. `_rpc_force_exit`(1087-1132)는 `_exit_lock` 안에서 `__exec_force_exit` → `Trade.commit()` → `wallets.update()`. `_rpc_force_entry`는 config `force_entry_enable`이 있어야 허용(1134-1136). `/stopbuy`는 현재 `_rpc_pause`로 통합됨.
- **REST**(`rpc/api_server/api_auth.py`): 단일 `username/password`(config) HTTP Basic → JWT(HS256, access 15분 / refresh 30일, `create_token`, 93-115). `http_basic_or_jwt_token`(118-131)이 모든 라우트의 dependency. **역할·권한 구분 없음** — 인증만 통과하면 `/forceexit`, `/stop`, `/reload_config` 등 전부 가능. WebSocket은 `ws_token` 또는 JWT(`validate_ws_token`, 57-87).
- **Telegram**(`rpc/telegram.py:93-135`): `@authorized_only` 데코레이터가 (1) `chat_id` 일치, (2) optional `topic_id`, (3) optional `authorized_users` 목록으로 필터. 실행 전 `Trade.rollback()`으로 다른 스레드 트랜잭션 오염 방지.

```python
# telegram.py:114-127
        chat_id = int(self._config["telegram"]["chat_id"])
        if cchat_id != chat_id:
            logger.info(f"Rejected unauthorized message from: {cchat_id}")
            return None
        ...
        authorized = self._config["telegram"].get("authorized_users", None)
        if authorized is not None and from_user_id not in authorized:
            logger.info(f"Unauthorized user tried to control the bot: {from_user_id}")
            return None
```

- 명령 실행에 대한 **감사 로그는 일반 logger 라인**뿐(누가/언제/무엇을 구조화 기록 없음). `_rpc_get_logs`(1487)는 in-memory buffer 조회.
- `ExternalMessageConsumer`(`rpc/external_message_consumer.py`) + `api_ws`로 **producer/consumer 봇 간 분석 DataFrame 공유**(한 봇이 분석, 여러 봇이 소비) — 다중 인스턴스 분업의 초기 형태.

### AIOS 시사점
- 4-상태 기계(RUNNING/PAUSED/STOPPED/RELOAD)와 "예외 타입이 상태 전이를 결정", "초기 상태 STOPPED", "PAUSED는 진입만 차단"은 그대로 가져갈 만한 운영 의미론. AIOS는 여기에 `DEGRADED`(데이터 지연/거래소 부분 장애)와 `LIQUIDATING`(강제 청산 진행)을 추가하는 것을 검토.
- RPC 권한이 단일 사용자·전권이라 기관용으로는 부족. AIOS는 명령별 RBAC(observer / operator / risk-officer), 2인 승인(force-exit all, reload), 명령 감사 이벤트(actor, channel, args, result, trade snapshot)를 필수로.
- 단일 스레드 루프 + 1개 Lock은 단순해서 버그가 적다. AIOS가 비동기·다중 워커로 가더라도 **주문 상태 변경은 직렬화된 단일 writer**를 유지하는 것이 안전하다.
- systemd watchdog 통합은 작지만 중요한 운영 디테일 — AIOS는 liveness/readiness + "마지막 성공 tick 시각"을 헬스로 노출해야 한다(`rpc.health`, `rpc.py:1789`가 `last_process`를 노출하는 것과 유사).

---

## 7. Data pipeline

### 7.1 DataProvider — `data/dataprovider.py`

- `refresh(pairlist, helping_pairs)`(460-474): 매 루프 `exchange.refresh_latest_ohlcv(final_pairs)` + `refresh_latest_trades`. informative pairs(`strategy.gather_informative_pairs`, `interface.py:1081`)가 `helping_pairs`로 합쳐져 같은 fetch 사이클에 포함.
- `get_analyzed_dataframe`(402-425): live/dry는 전체 DF, 백테스트는 `__slice_index`로 **현재 시각까지의 슬라이스(최대 1000캔들)** 만 노출 — 백테스트 lookahead 방지의 구조적 장치.
- `_emit_df`(121) / `_add_external_df`(174) — producer/consumer 간 DataFrame 전송(Section 6.3).
- `check_delisting`(632): `has_delisting` flag 거래소에서 상장폐지 예정 조회.
- 캔들 fetch는 ccxt.pro websocket(`exchange_ws.py`, `ws_enabled` flag가 True인 거래소만) 또는 REST 폴링(`refresh_latest_ohlcv`, `exchange.py:2885`), 캐시 TTL 기반 `_now_is_time_to_refresh`(2980).

### 7.2 Pairlists — `plugins/pairlist/`, `pairlistmanager.py`

`refresh_pairlist`(`pairlistmanager.py:137-175`): 체인의 첫 handler가 **generator**(`gen_pairlist`), 나머지는 **filter**(`filter_pairlist`), 마지막에 blacklist 검증. 21개 플러그인: Static/Volume/MarketCap/PercentChange/Remote/Producer/CrossMarket(생성기), Age/Delist/Precision/Price/Spread/Volatility/RangeStability/Performance/FullTrades/Offset/Shuffle/PairInformation(필터). `VolumePairList`는 `refresh_period`(기본 1800s) TTL 캐시, `min_value`, lookback 기반 range 모드. 각 플러그인은 `available_parameters()`로 파라미터 스키마를 선언(`IPairList.py:127`)해 UI가 자동 렌더링. `SupportsBacktesting` enum(`IPairList.py:60`)으로 백테스트 호환 여부를 명시.

### 7.3 데이터 품질

- `ohlcv_fill_up_missing_data`(`data/converter/converter.py:126-175`): 결측 캔들을 resample로 만들고 close forward-fill, OHL=close, volume=0. 결측률 1% 초과면 info 로그. funding-rate 같은 single-value 타입은 "inventing one would silently make up rates"라며 건드리지 않음.
- `clean_ohlcv_dataframe`(86): 중복 제거·정렬. `validate_backtest_data`(`history/history_utils.py:679-704`): 기대 프레임 수 대비 부족분 경고(차단은 아님).
- `validate_required_startup_candles`(`exchange.py:896`): startup_candle_count가 거래소 `ohlcv_candle_limit`을 넘으면 거부. `validate_informative_candle_types`(`freqtradebot.py:262`).
- `strategy_validation.py` / `disable_dataframe_checks`: 전략이 DataFrame 길이·마지막 날짜를 바꾸면 에러(lookahead 방지).

### AIOS 시사점
- generator→filter chain + 파라미터 스키마 자기선언은 universe 관리의 좋은 템플릿. AIOS는 여기에 **universe 스냅샷 버전**(어느 시각에 어떤 필터 결과였는지)을 기록해 재현성을 확보해야 한다.
- 결측 캔들 forward-fill은 편리하지만 "합성 데이터" 표시가 없다. AIOS는 합성 캔들에 `is_synthetic` 플래그를 남기고, 결측률이 임계 초과면 해당 pair를 자동으로 DEGRADED 처리하는 정책이 필요하다.
- 백테스트에서 DataProvider가 슬라이스만 노출하는 설계는 lookahead 방지의 구조적 해법 — AIOS의 데이터 접근 API도 "as-of time" 파라미터를 강제하는 형태가 좋다.

---

## 8. 기관용 관점에서 주목할 점 / 명시적으로 없는 것

**있는 것 (참고 가치)**
- 예외 타입 → 재시도/상태 전이 정책의 일관된 결합(§1, §6).
- Dry-run에서 credential 물리 제거 + DB 분리(§2).
- 도메인 로직(Trade/Order/Wallet/Protection)이 backtest/dry/live에 걸쳐 단일 코드(§3, §5).
- lookahead/recursive 편향 검출 도구(§5.2), 전략 예외 격리 래퍼(§4.4), 전략 실행 시간 경고.
- `WalletHistory` 일일 잔고 스냅샷, `record_version` 행 버전, `ft_cancel_reason` 문자열 사유(`constants.py:209-220` `CANCEL_REASON`), `ExitType` enum(`enums/exittype.py`)으로 종료 사유 표준화.
- `ft_client/`(REST 클라이언트 패키지), FastAPI OpenAPI 스키마(`api_schemas.py`), producer/consumer 봇 간 데이터 공유.
- 테스트 140 파일, `tests/exchange_online`으로 실거래소 통합 테스트 분리.

**없는 것 (AIOS가 반드시 채워야 할 갭)**
- **Multi-tenancy / 다중 계정**: 프로세스당 1 거래소·1 계정·1 전략·1 DB. `Trade` 테이블에 tenant/account/strategy 컬럼 없음(`exchange`, `pair`만). 다중 전략은 프로세스를 여러 개 띄우고 DB를 분리하는 방식.
- **Audit trail**: 구조화된 감사 이벤트 없음. 주문 변경 이력은 `Order` 행의 현재 상태만(과거 상태 덮어씀), RPC 명령은 텍스트 로그.
- **Idempotency key / clientOrderId**: `create_order`에 client order id를 넣지 않음(`grep clientOrderId` → `bitget.py:93`의 stop-order 조회에서만 사용). 재시도·중복 제출 방어는 "create_stoploss는 재시도 0회" 같은 관습에 의존. 네트워크 timeout 후 주문이 실제로 들어갔는지는 다음 루프의 `handle_onexchange_order`/잔고 불일치로 사후 발견.
- **RBAC / 승인 워크플로**: 단일 사용자 JWT, Telegram chat_id.
- **Pre-trade risk**: 노출 한도, 일중 손실 한도, 주문 빈도·notional 한도, fat-finger 체크 없음(min/max stake는 거래소 한도만, `_get_stake_amount_limit`, `exchange.py:1098`).
- **Rate limiter / circuit breaker**: ccxt 위임, 전역 상태 없음.
- **Secrets 관리**: config JSON 평문(환경변수 `FREQTRADE__*` 치환은 지원).
- **관측성**: 메트릭 export(Prometheus 등) 없음, 로그는 텍스트/JSON formatter(`loggers/json_formatter.py`).
- **시계·시간 동기**: 거래소 time drift 보정 없음(ccxt `adjustForTimeDifference` 옵션에 의존).

### AIOS 시사점
- Freqtrade는 "1 프로세스 = 1 계정·전략"이라는 단순성으로 신뢰성을 얻었다. AIOS가 multi-tenant로 가더라도 **실행 단위(execution cell)는 그 단순성을 유지**하고, 상위 control plane이 셀을 오케스트레이션하는 구조가 안전하다.
- 모든 주문에 deterministic `clientOrderId`(tenant/strategy/intent hash)를 부여하고, 제출 전 intent를 먼저 영속화(write-ahead)하는 것이 Freqtrade 대비 가장 큰 개선점이다.
- 감사·재현성: OrderEvent append-only 로그 + 전략 아티팩트 해시 + universe 스냅샷 + 데이터 as-of — Freqtrade의 `record_version`, `ExitType`, `CANCEL_REASON` 표준화는 이 위에 얹을 어휘로 재사용.
- GPL-3.0이므로 코드가 아닌 위 패턴만 가져가고, ccxt(MIT)는 직접 의존해도 무방하다.
