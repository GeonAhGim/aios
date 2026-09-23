---
name: review-exchange
description: 거래소 어댑터 — fail-closed, UNKNOWN 재조회, 계약 테스트, capability-gated, outbox 내구성 축 검토 체크리스트
paths:
  - "src/exchanges/**"
  - "src/services/oms/**"
tier: [M]
axis: exchange
---

# review-exchange

## 적용 조건

`src/exchanges/{bitget,kis,nh,common,paper}/`, `src/services/oms/`를 건드릴 때 적용한다
(tier M, tier S로 격상되는 조건: outbox/inbox 상태기계나 주문 제출 경로 변경).
`docs/design/02_exchange_adapter_v1.3.md`, `docs/specs/L4_execution_oms_and_exchange_v1.0.md`
§1(R10)·§4·§5, INVARIANTS I-02·I-03이 규범이다.

## 체크리스트

1. 응답 유실 주문(UNKNOWN 상태)을 실패로 단정하지 않고 `clientOid`로 거래소 실제 상태를
   재조회한다 — 재조회 상한 초과 시 CRITICAL 로그만 남기고 넘어가지 않고 해당 스코프를
   정지한다 [근거: spec:L4_execution_oms_and_exchange_v1.0.md#R10] [검사: 후보]
2. "전송 중 크래시"(요청은 나갔는데 응답 저장 전 프로세스가 죽는 경우)가 outbox 패턴으로
   복구된다 — 재시작 시 PENDING outbox 행을 재조회 후 재개하지, 재전송으로 중복 주문을
   내지 않는다 [근거: spec:L4_execution_oms_and_exchange_v1.0.md#R10] [검사: 후보]
3. 미등록 심볼은 `SymbolRegistry.to_venue`/`to_canonical`에서 `UnknownSymbolError`로
   fail-closed 거부되고, 임의 문자열 변환으로 통과시키지 않는다
   [근거: spec:L4_execution_oms_and_exchange_v1.0.md#2-A] [검사: 후보]
4. 거래소별 기능 차이는 `VenueCapabilityProfile`로 선언되고, 어댑터 코드가 지원하지 않는
   기능을 호출하면 컴파일/런타임에서 명시적으로 거부한다(조용히 무시하거나 다른 기능으로
   대체하지 않는다) [근거: spec:L4_execution_oms_and_exchange_v1.0.md#2.0-A] [검사: 후보]
5. 멱등키 스코프가 `(tenant, actor, route, content-hash)` 4중이고, 같은 키에 다른 payload가
   오면 재전송으로 처리하지 않고 거부(409류)한다 [근거: I-03]
   [검사: 후보]
6. 쓰기 경로가 조건부 UPDATE 또는 `FOR UPDATE`를 쓴다 — 동시 두 프로세스가 같은 주문/
   outbox 행을 갱신해도 경쟁 상태로 이중 전송이 나지 않는다
   [근거: 105번] [검사: scripts/check_consistency.py::check_router_wiring]
7. 계약 테스트(contract test)가 거래소 응답의 실제 스키마 변경(신규 필드 추가는 minor,
   제거·의미 변경은 신규 버전 모듈)을 검증하고, 프로덕션 코드가 계약 밖 필드에 암묵
   의존하지 않는다 [근거: spec:L4_execution_oms_and_exchange_v1.0.md#3.3] [검사: 후보]
8. rate limit(토큰 버킷 등)이 실제 시간 경과를 기다리는 방식으로 검증된다 — sleep을
   모킹해 즉시 통과시키는 테스트만 있고 실제 처리량 상한을 재현하지 않는 경우가 없다
   [근거: DC-12] [검사: 후보]
9. 사용자 입력(주문 메모, 심볼 별칭 등)이 거래소로 나가는 요청에 그대로 삽입되기 전에
   인코딩/이스케이프를 거친다 — 비ASCII/특수문자 주입으로 API 요청 구조가 깨지지 않는다
   [근거: git:9315526c] [검사: 후보]
10. outbox/inbox 재확인이 필요한 명령(`get_order` 등)이 스케줄 루프에서 배선 누락으로
    호출되지 않는 경로가 없다 — 함수는 존재하는데 아무도 호출하지 않는 "구현됨≠배선됨"
    상태가 아니다 [근거: I-10, L4-18] [검사: 후보]
11. 인증 실패(401 등)·네트워크 재시도 시 노출되는 자격증명이 로그에 평문으로 남지 않는다
    [근거: 07_logging_config_v1.3.md] [검사: 후보]
12. 다중 인스턴스(같은 계정을 여러 워커/프로세스가 동시에 사용) 상황에서 주문 상태 조회·
    outbox 재개가 서로 덮어쓰지 않는다(D3 동시성 증거) [근거: DC-12] [검사: 후보]

## 반례

### 반례 1 — UNKNOWN 응답을 실패로 단정

```python
# BAD: 타임아웃/UNKNOWN을 실패로 단정해 재시도 없이 다음 주문을 내보낸다 -- 중복 체결 위험.
async def submit(self, order: OrderCommand) -> SubmitResult:
    try:
        return await self._client.place_order(order)
    except TimeoutError:
        return SubmitResult(status="FAILED")  # 실제로는 거래소에 접수됐을 수 있다

# GOOD: UNKNOWN이면 clientOid로 재조회 후 실제 상태를 확정한다.
async def submit(self, order: OrderCommand) -> SubmitResult:
    try:
        return await self._client.place_order(order)
    except TimeoutError:
        return await self._reconcile_by_client_oid(order.client_oid)
```

### 반례 2 — 미등록 심볼을 원문 그대로 통과

```python
# BAD: 등록되지 않은 심볼도 대문자 변환만 해서 거래소로 그대로 보낸다.
def to_venue(self, canonical: str, venue: str) -> str:
    return self._table.get((canonical, venue), canonical.upper())

# GOOD: 미등록이면 fail-closed 거부.
def to_venue(self, canonical: str, venue: str) -> str:
    key = (canonical, venue)
    if key not in self._table:
        raise UnknownSymbolError(canonical, venue)
    return self._table[key]
```

## 이 축에서 났던 사고

- NH 어댑터의 `recover_stuck_outbox_commands`가 구현은 있었지만 스케줄 루프에
  배선되지 않아 정지된 outbox 명령이 영원히 복구되지 않던 결함(REJECT 반영 후 수정)
  (git:89f4fb70)
- KIS 어댑터에 한글(비ASCII) 문자열 주입 시 요청 구조가 깨질 수 있던 취약점에 가드를
  추가 (git:9315526c)
- KIS 토큰버킷 처리량 테스트가 sleep을 모킹해 실제 rate limit을 검증하지 못하던 결함을
  실시간 대기 주입으로 수정 (git:89f70e8f)
