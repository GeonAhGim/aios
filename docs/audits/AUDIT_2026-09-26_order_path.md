# 주문 경로 적대적 감사 — task-7978

날짜: 2026-09-26. 범위: `src/services/oms/**`(submit/cancel/modify, outbox/inbox, 3-way
reconciler), `src/exchanges/{bitget,kis,binance,okx}/trading_mixin.py`, OMS→
`src/foundation/positions`/legacy `positions` 반영 경로. 이 리프는 **발견을 만드는 리프**다
— 코드 변경 없음(재현용 테스트 지적만, 신규 테스트 파일은 추가하지 않았다 — 기존
`tests/integration/oms/test_three_way_reconciler.py`가 재현에 필요한 골격을 이미 갖추고
있어, §4 각 발견에 "어떤 기존 테스트를 어떻게 변형하면 재현되는지"를 적는 것으로 대신했다).

## 0. 요약

| # | 심각도 | 제목 |
|---|---|---|
| F1 | **S** | 거래소가 확정 취소한 주문이 `orders.status`를 영원히 CANCELLED로 전이하지 못한다 — 3-way reconciler가 이를 MATERIAL_MISMATCH로 잡아내지만 자동 해소 경로가 없어, 정상적인 취소 1건이 테넌트 전체의 신규 제출을 무기한 차단한다 |
| F2 | **S** | Binance/OKX 어댑터에 `get_order`가 구현되어 있지 않다(Protocol stub) — UNKNOWN 해소(`unknown_resolver`)·재시작 복구·모든 clientOid 역조회가 이 두 venue에서 구조적으로 불가능하다 |
| F3 | **S** | 부분체결 후 취소된 주문은 포지션 원장에 전혀 반영되지 않는다 — `record_fill_in_position_ledger`는 주문이 터미널 `FILLED`에 도달했을 때만 호출된다 |
| F4 | M | 4개 어댑터 전부 tick size/lot size/min notional 사전검증이 없다 — 전량 거래소 거부에 위임 |
| F5 | M | KIS·Binance는 canonical 심볼을 검증·변환 없이 그대로 거래소로 전달한다(Bitget/OKX는 `symbol_normalizer` fail-closed 경유) |
| F6 | M | KIS는 client_order_id 개념 자체가 없다 — OMS의 멱등 재시도·중복응답 채택(F14)이 KIS에서 구조적으로 무력화된다 |
| F7 | L | `record_fill_in_position_ledger`가 `fill_seq=1`을 하드코딩한다 — 지금은 F3의 게이팅 덕에 은폐돼 있으나, 원장 표준 멱등키 형식(`fill:{order_id}:{fill_seq}`)의 계약을 지키지 않는다 |
| F8 | L | Bitget의 `find_order_by_client_id`(유일한 clientOid 역조회 구현)가 "미검증"이고, `place_order`/`cancel_order` 자신은 이를 호출하지 않는다 — UNKNOWN 해소는 외부 호출자(`unknown_resolver`)에만 의존 |

대조표(§1)의 O/X 칸이 모두 O가 아니므로 "발견 0건" 조건은 성립하지 않는다.

## 1. 계약 대조표 — 어댑터 4종 × 항목

| 항목 | Bitget | KIS | Binance | OKX |
|---|---|---|---|---|
| 멱등키(clientOid류) 거래소 전달 | O — `clientOid`, `trading_mixin.py:81` | **X** — 개념 없음, `get_order`가 `client_order_id=""` 하드코딩, `trading_mixin.py:173` (F6) | O — `newClientOrderId` + 비어있지 않음 사전검증, `trading_mixin.py:85-93,130` | O(값 전달은 O) — `clOrdId`, `trading_mixin.py:178`. 단 Binance와 달리 비어있지 않음 사전검증이 없음(**?**) |
| canonical→venue 심볼 변환(fail-closed) | O — `symbol_normalizer` 경유, 미등록 quote는 `SymbolNormalizationError`, `trading_mixin.py:77`, `symbols.py:26-28` | **X** — `order.symbol`을 검증 없이 `PDNO`로 그대로 전달, `trading_mixin.py:74`(F5). `symbol_normalizer.py:97-100`에 KRX 검증기가 존재하지만 호출되지 않음 | **X** — 변환 함수 자체가 없음(`symbol_normalizer.py`에 `Venue.BINANCE` 항목 없음, `src/exchanges/binance/symbols.py` 파일 없음), `trading_mixin.py:126`(F5) | O — `symbol_normalizer` 경유 + 이미 venue 형식인 심볼(슬래시 없음)은 명시적으로 거부, `trading_mixin.py:97-108`, `symbols.py:29-31` |
| 수량/가격 tick·lot·min notional 사전검증 | **X** — 전무, `trading_mixin.py:76-92` | **X** — 전무, `trading_mixin.py:74-79` | **X** — `>0`/유한성만, `trading_mixin.py:75-82` | **X** — `>0`만, `trading_mixin.py:88-94` |
| 부분체결·취소 경합 처리 | ? — 상태 어휘(`_STATUS_MAP`, `trading_query_mixin.py:47-58`)는 PARTIALLY_FILLED/FILLED/CANCELLED를 정확히 구분하지만 `cancel_order`가 취소 후 재조회로 이를 확인하지 않음, `trading_mixin.py:101-112` | ? — `get_order`(`:160-168`)는 정확히 구분하지만 `cancel_order`가 호출하지 않음, `:118-120` | **X** — `cancel_order`가 `status=="CANCELED"`만 성공으로 보고, 실제 "취소 시점 부분체결" 응답도 `False`로 뭉갬. 확인할 `get_order` 자체가 없음(F2), `trading_mixin.py:150-155` | **X** — `cancel_order`는 `sCode` 비정상 시 예외(가장 양호)지만 사후 재조회 불가(F2), `modify_order`가 존재하지 않는 `self.get_order()`를 호출(`:225`) |
| 5xx/타임아웃/중복/부분응답 → clientOid 역조회 재확인 | ? — `find_order_by_client_id` 존재(`trading_query_mixin.py:179-190`)하나 "미검증", `place_order`/`cancel_order`에서 자체 호출 없음(F8) | **X** — 구조적으로 불가(F6) | **X** — 대응 메서드 전무(F2) | **X** — 대응 메서드 전무(F2) |

## 2. 선례 대조 — Bitget이 지키는 것 중 나머지 3개에 빠진 것

Bitget은 4개 중 유일하게 (a) `symbol_normalizer` 기반 fail-closed 심볼 변환과 (b) clientOid
역조회 메서드(`find_order_by_client_id`)를 **둘 다** 갖춘 어댑터다. 이 두 축을 기준으로:

- **심볼 변환**: OKX는 이미 Bitget과 동등(§1). KIS·Binance만 결여(F5) — 특히 Binance는
  `Venue` enum에 항목조차 없어 "구현 중 누락"이 아니라 "설계에서 빠짐"에 가깝다.
- **clientOid 역조회**: KIS·Binance·OKX 어느 쪽도 갖고 있지 않다(F2, F6). Bitget 자신도
  "미검증"이고 자기 제출/취소 경로에서 스스로 쓰지 않는다는 점에서(F8) 완전한 선례는
  아니지만, 적어도 *메서드가 존재*하는 것은 Bitget뿐이다.
- **상태 어휘 완결성**: Bitget/KIS는 PARTIALLY_FILLED/FILLED/CANCELLED를 구분하는 `get_order`
  가 있다(단, 취소 경합 재확인에 배선되지 않음 — §1). Binance/OKX는 그 전 단계인 `get_order`
  자체가 없다(F2) — Bitget/KIS보다 한 단계 더 낮은 성숙도.

## 3. 실패 주입 결과

### F1 — 거래소 확정 취소가 `orders.status`에 반영되지 않음, reconciler가 영구 차단으로 확대

- 근거 파일:줄:
  - `src/services/oms/domain/state_machine.py:157-158` — `(ACKNOWLEDGED|PARTIALLY_FILLED, VENUE_CANCELLED) -> CANCELLED` 전이가 **정의되어 있음**.
  - `OrderEvent.VENUE_CANCELLED`의 실제 호출부는 저장소 전체에서 `src/foundation/ems/application/aggregate_parent.py:60`(다른 바운디드 컨텍스트, OMS 주문 자체를 전이시키지 않음) 뿐이다 — OMS 쪽(`inbox_processor.py`, `outbox_commands.py`, `three_way_reconciler.py`, `unknown_resolver*.py`) 어디에도 `next_status(..., OrderEvent.VENUE_CANCELLED)` 호출이 없다.
  - `src/services/oms/application/inbox_processor.py:198-200` — inbox가 처리하는 건 체결 이벤트뿐이다: `if ev.last_fill is None: await self._mark_ignored(...); return None`("이 리프는 체결 이벤트만 다룬다"). 거래소가 보내는 취소 확인(체결 없는 상태 이벤트)은 여기서 **무조건 IGNORED로 폐기**된다.
  - `src/services/oms/application/outbox_commands.py:12-14`(모듈 docstring) — "취소 결과 상태(VENUE_CANCELLED)는 여기서 전이하지 않는다 — 거래소 이벤트가 inbox(L4-15)로 들어와 확정한다"고 **스스로 inbox에 위임을 명시**하지만, 위에서 확인했듯 inbox는 그 이벤트를 처리하지 않는다 — 위임 대상이 비어 있다.
  - `src/services/oms/application/restart_recovery.py:15` — "③ (RESYNC of non-terminal orders) is still out of scope (decision)"라고 **문서화된 기존 결정**이다. 즉 "정정 안 됨"은 알려진 갭이지만, 이 감사에서 새로 확인한 것은 그 갭의 **파급 효과**다(아래).
- 파급 효과(재현): `three_way_reconciler.py`의 `compare_triple`(`reconcile_rules.py:42-52`)은 내부 주문이 provider의 open-orders 목록에 없으면 `ORDER_MISSING_AT_PROVIDER`/`MATERIAL_MISMATCH`를 정확히 잡아낸다 — 즉 "거래소에서 성공적으로 취소된 주문"은 다음 reconcile 주기에서 곧바로 MATERIAL_MISMATCH로 분류된다. `_apply_account_gate`(`three_way_reconciler.py:127-165`)는 MATERIAL_MISMATCH가 있는 한 ACCOUNT 스코프 safety control을 ACTIVE로 유지하고, `submit_order`의 `pre_submit_gate`는 이 control이 ACTIVE면 항상 DENY한다(`test_reconcile_material_mismatch_denies_and_resolving_allows_submit`, `tests/integration/oms/test_three_way_reconciler.py:209-265`가 이 차단 메커니즘 자체는 검증한다). 그런데 이 control을 해제하는 유일한 경로는 "다음 reconcile에서 discrepancy가 사라지는 것"뿐이고, discrepancy가 사라지려면 내부 주문의 `status`가 실제로 바뀌어야 하는데 — 그 상태 전이 코드가 없다(위 근거). 결과적으로 **정상적인 주문 취소 1건이 그 테넌트의 모든 신규 제출을 무기한 차단**하는 라이브니스 버그다.
- 재현 스크립트(수동, 실행은 안 함 — 재현 절차만 기술): `test_reconcile_material_mismatch_denies_and_resolving_allows_submit`(`tests/integration/oms/test_three_way_reconciler.py:209`)를 변형해, `_insert_open_order`로 만든 주문을 실제 `cancel_order()` 경로로 취소 요청하고 `_ScriptedAdapter`가 그 주문을 `open_orders=[]`로 반환하도록 하면(= 거래소가 정상적으로 취소를 반영한 상태), `reconcile_account` 호출 결과가 `MATERIAL_MISMATCH`로 나오고, 그 뒤 몇 번을 다시 `reconcile_account`를 호출해도(어댑터가 계속 `open_orders=[]`를 반환) `HEALTHY`로 돌아오지 않는다 — 현재 코드베이스에 이를 되돌리는 경로가 없기 때문이다. 기존 테스트는 "값이 다시 일치하면 해제된다"만 증명했지, "취소로 사라진 경우도 해제된다"는 증명하지 않는다(그런 테스트 자체가 저장소에 없음 — `grep -rl "ORDER_MISSING_AT_PROVIDER" tests`는 `tests/unit/oms/test_reconcile_rules.py`(순수 함수 단위 테스트)만 반환하고, 통합 레벨에서는 없다).
- 제안 조치: `inbox_processor` 또는 별도 소비자가 거래소의 "취소 확정" 이벤트(체결 없는 venue_status=CANCELLED류)를 받아 `OrderEvent.VENUE_CANCELLED`로 전이시키는 경로를 신설하거나, 최소한 `three_way_reconciler`가 `ORDER_MISSING_AT_PROVIDER`를 발견했을 때 그 주문이 CANCEL_REQUESTED 이력이 있으면 자동으로 CANCELLED로 정정하는 좁은 자기치유 경로를 추가한다. `restart_recovery.py`가 이미 "③ RESYNC out of scope"라고 결정해 둔 부분과 같은 스코프이므로, 이 리프가 그 결정을 재검토하는 계기가 되어야 한다.
- 예상 리프 크기: M(상태 전이 배선 1곳 + reconciler 자기치유 분기 + 통합 테스트 2~3개, 기존 파일 수정 위주).

### F2 — Binance/OKX `get_order` 미구현

- 근거: `src/exchanges/binance/trading_mixin.py:111-117`(Protocol 선언만, "not yet implemented for Binance — tests supply a stub"), `src/exchanges/okx/trading_mixin.py:158-164`("get_order lives in account_mixin.py, task BR-21c" — 미착수). `src/exchanges/okx/trading_mixin.py:225`의 `modify_order`가 존재하지 않는 `self.get_order(order_id)`를 호출 — 실제 OKX 정정을 시도하면 `AttributeError`(또는 Protocol 위반) 즉시 발생.
- 파급: `unknown_resolver.py:114`의 `adapter.find_order_by_client_id(...)`, `resolve_unknown`의 `_is_still_open`(`:73-79`, `adapter.get_open_orders`)은 별개 메서드라 영향 없지만, F1과 별개로 UNKNOWN 해소 자체가 Bitget 외 venue에서 동작하려면 `find_order_by_client_id`가 필요하고(모듈 docstring: "이 리프는 F5-a(Bitget)만 다룬다" — KIS/NH는 `UnsupportedCapabilityError`로 의도적 fail-closed, decision 문서화됨), Binance/OKX는 그 결정에 애초에 포함되지 않았다 — 즉 Binance/OKX가 UNKNOWN에 빠지면 자동 해소 경로가 전혀 없다(문서화된 결정 밖의 사각지대).
- 재현: `src/exchanges/okx/trading_mixin.py`의 `modify_order`를 실제 `send_modify`(`outbox_commands.py:130`) 경로로 호출하면 `adapter.modify_order(...)` 내부의 `self.get_order(order_id)` 호출에서 즉시 실패한다 — 단위 테스트로 `TestOkxTradingMixin.modify_order` 호출 시 `AttributeError`/`NotImplementedError` 발생을 단언하는 테스트를 추가하면 즉시 재현된다(신규 테스트는 이 리프에서 추가하지 않음 — 정정 리프가 만들 때 같이 추가 권장).
- 제안 조치: Binance/OKX에 `get_order` 구현(계정 조회 믹스인 배선), 완료 전까지는 `modify_order`가 `UnsupportedCapabilityError`를 던지도록 `VenueCapabilityProfile.supports_modify=False`로 낮추는 임시 조치 검토.
- 예상 리프 크기: M(어댑터당 별도 리프 권장 — Binance 1개, OKX 1개, 각각 S~M).

### F3 — 부분체결 후 취소 시 포지션 원장 미반영

- 근거: `src/services/oms/application/inbox_processor.py:246` — `_process_row`는 `return order_id if new_status is OrderStatus.FILLED else None`로, **터미널 FILLED로 확정될 때만** `order_id`를 반환하고, 그 반환값만 `_apply_position_ledger`(`:270-279`, `record_fill_in_position_ledger` 호출)로 이어진다(`:122-124`, `:144-146`). PARTIALLY_FILLED로만 남고 이후 CANCELLED(또는 F1로 인해 상태가 아예 안 바뀌는 경우까지 포함)로 끝나는 주문은 이 함수가 **한 번도 호출되지 않는다**.
- `src/services/order_service/position_ledger.py:43` — `record_fill_in_position_ledger` 자신도 `if order.status != OrderStatus.FILLED ...: return`으로 같은 게이트를 이중으로 건다.
- 재현: 수량 10 주문을 제출해 3만 체결(PARTIALLY_FILLED) 후 취소하는 시나리오를 `tests/integration/oms/test_cancel_order.py`(부분체결 취소 테스트가 있다면 그 픽스처)에 이어 붙여 `pos_snapshot`/`positions` 테이블을 조회하면, 체결된 3수량에 대응하는 포지션/수수료 행이 전혀 없음을 확인할 수 있다. 실거래소에서는 이 3만큼의 실제 보유량·수수료가 발생했으므로 내부 원장과 실물의 괴리가 영구화된다.
- 제안 조치: 부분체결 자체(터미널 도달 여부 무관)를 원장에 반영하도록 `_process_row`가 체결이 새로 삽입될 때마다(`inserted` 판정 직후, `:203-206`) `_apply_position_ledger`류 호출을 트리거하게 하고, `record_fill_in_position_ledger`의 FILLED 전용 게이트를 완화하거나 별도의 "부분체결 반영" 경로를 신설한다. `RecordFillCommand.fill_seq`(F7과 연동)도 함께 정정 필요.
- 예상 리프 크기: L(원장 반영 시점 변경은 I-04/105번 표준 영향 범위가 넓어 D3 증거 요구 축 — 신중한 별도 스코핑 필요).

## 4. 동시성

- **같은 client_order_id 동시 제출**: `submit_order.py:159-274`가 이미 설계·주석 수준에서
  상세히 다룬다 — `orders.client_order_id` UNIQUE 제약이 실제 동시성 관문이고, 패자는
  `UniqueViolationError` → 자기 tx 롤백 → 승자 행 재조회(`resolve_after_collision`)로
  귀결된다(`submit_order.py:22-31`). `tests/adversarial/oms/test_concurrent_submit.py`가
  이를 커버한다 — 기존 테스트 확인, 이 축은 **양호(O)**.
- **취소-체결 경합**: `cancel_order.py`는 `get_for_update`로 행을 잠그고 `next_status`가
  ACKNOWLEDGED/PARTIALLY_FILLED에서만 CANCEL_REQUESTED 자기루프를 허용하므로, DB 레벨의
  경합 자체는 안전하다(같은 tx 안에서 순서가 강제됨). 그러나 실제 거래소 레벨의 취소-체결
  경합(주문이 취소 요청을 보내는 순간 이미 거래소에서 체결됨)은 §1/§3(F1)에서 지적한 대로
  4개 어댑터 중 어느 것도 사후 재조회로 확정하지 않는다 — **경합의 DB 측 정합성은 O, 거래소
  측 정합성 확인은 X**(F1/§1과 동일 근거, 중복 기재하지 않음).
- **reconciler 2인스턴스**: `tests/integration/oms/test_three_way_reconciler.py:370
  test_concurrent_reconciler_instances_only_one_reconciles_same_account`가 존재하고
  통과를 전제로 한다(이 리프에서 실행하지 않았으나 파일 존재 자체가 D3 요건 충족 근거) —
  이 축은 **양호(O)**로 판단.
- `tests/integration/oms/test_concurrent_dispatchers.py`가 outbox 디스패처 다중 워커
  경합도 별도로 커버한다 — **양호(O)**.

## 5. 결론

주문 제출(submit)·outbox 동시성·idempotency scope 설계는 성숙하다(O 다수). 반면 **취소
확정의 왕복(거래소→내부 상태)**, **4개 어댑터의 tick/lot 사전검증**, **KIS/Binance의 심볼
검증**, **Binance/OKX의 주문 조회 자체**는 구조적 공백이다. 그중 F1은 코드 결함이 정상
운영 흐름(주문 취소)에서 계정 전체 거래 정지로 확대되는 경로가 실증됐다는 점에서 가장
시급하다.
