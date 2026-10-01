# 감사: 데이터 수집·재생 적대적 감사 (5/6)

- **날짜:** 2026-10-01
- **워커:** claude-local-3
- **작업:** task-10439
- **범위:**
  - `src/foundation/market_data/application/ingest_candles.py`
  - `src/foundation/market_data/application/ingest_ticks.py`
  - `src/foundation/market_data/application/backfill_job.py`
  - `src/foundation/market_data/application/replay_candles.py`
  - `src/foundation/market_data/application/get_candles.py`
  - `src/foundation/research_data/application/ingest_job.py`
  - `src/foundation/research_data/application/query.py`
  - `src/foundation/market_data/domain/corporate_actions.py`
  - `src/foundation/market_data/domain/calendar/known_venues.py`
  - `src/foundation/research_data/domain/as_of_binding.py`
  - `src/core/eventstore/replay.py`
  - `scripts/replay_verify.py`
- **선행:** task-9457 (인증·RLS 정착)

## §0 요약 표

| ID   | 심각도 | 파일:줄              | 내용                        | 재현                          | 조치 제안              |
|------|--------|----------------------|-----------------------------|-------------------------------|------------------------|
| F1   | M      | md_candle, md_tick   | tenant_id 누락              | DB 스키마 확인                | tenant_id 추가 FK      |
| F2   | M      | backfill_job.py:158  | backfill↔실시간 동시 구간   | 동시 백필+실시간 테스트       | SELECT ... FOR UPDATE   |
| F3   | M      | ingest_ticks.py:106  | trade_id 중복 시 upsert 덮어쓰기 | 중복 trade_id 재전송 테스트 | quarantine 또는 ignore   |
| F4   | M      | as_of_binding.py:21  | default_as_of() 결정성 문제   | datetime.now() patch 테스트   | clock 의존성 주입       |
| F5   | L      | get_candles.py:123   | as_of==close_time 경계       | 경계값 테스트                 | spec 명시 또는 ≤로 변경  |
| F6   | L      | backfill_job.py:148  | partition 중복 생성 오버헤드  | 짧은 구간 여러 번 백필        | skip_if_exists 옵션      |
| F7   | L      | replay_candles.py:83 | 시계·난수 시드 의존 누락      | CI hash-seed 환경에서 재생    | seed 주입 계약 추가      |

## §1 선례 대조

### 1.1 주문 경로 (task-7978) 계약

- 명시적 tenant 바인딩: `order` 테이블에 `tenant_id` 컬럼 + FK
- 조건부 쓰기: 상태 전이 테이블 제약 (CHECK + trigger)
- 실패 시 거부: 모든 쓰기가 조건부 UPDATE / SELECT FOR UPDATE
- 감사 이벤트: `foundation_audit_event` 테이블에 기록

### 1.2 원장·회계 (task-8675) 계약

- 낙관적 잠금: `version` 컬럼 + 조건부 UPDATE
- fail-closed: 트랜잭션 실패 시 롤백 + 에러 이벤트
- Decimal 금액: 모든 monetary 필드가 NUMERIC(38,10)

### 1.3 리스크·서킷브레이커 (task-8882) 계약

- 실패 시 거부: 리스크 제한 위반 시 주문 거부
- 감사 이벤트: `foundation_audit_event`에 `risk_check` 타입

### 1.4 인증·RLS (task-9420) 계약

- RLS 정책: 모든 tenant-isolated 테이블에 row-level security
- tenant 바인딩: 모든 쿼리에 `current_setting('app.tenant_id')`

## §2 발견 상세

### F1: md_candle / md_tick 테이블에 tenant_id 누락 [M]

**위치:** `src/db/migrations/versions/4a1d0c0de008_md_candles.py` md_candle:142-166, md_tick:194-208

**문제:**
- `md_ingest_batch` 테이블에는 `tenant_id UUID REFERENCES users(user_id)` 컬럼이 있지만(121줄),
  실제 데이터 테이블 `md_candle`와 `md_tick`에는 `tenant_id` 컬럼이 없다.
- `md_candle`의 PRIMARY KEY는 `(venue, instrument_id, timeframe, open_time)`이고
  `md_tick`의 UNIQUE는 `(venue, instrument_id, trade_id, traded_at)` — 둘 다 tenant_id 없음.
- 인증 경로(task-9420)의 RLS 정책은 tenant_id 기반 row-level security를 정의하지만,
  md_candle/md_tick에 tenant_id가 없으므로 RLS를 적용할 수 없다.
- **결과:** 멀티테넌트 환경에서 A사의 수집 데이터가 B사 조회에 누설될 수 있음.

**선례 대조:**
- 주문 경로는 `order.tenant_id` 명시적 FK + RLS 정책
- 원장은 `ledger_entry.tenant_id` + 낙관적 잠금
- 데이터 수집은 tenant_id 없음 = 계약 불일치

**재현:**
```sql
-- tenant A가 수집한 candle를 tenant B가 조회 가능
SELECT count(*) FROM md_candle WHERE instrument_id = 'some_uuid';
-- tenant_id 필터 없음 → 모든 tenant의 데이터 조회됨
```

**조치 제안:**
1. `md_candle`와 `md_tick`에 `tenant_id UUID REFERENCES users(user_id)` 추가
2. 기존 RLS 정책 갱신 또는 새 정책 발행
3. ingest_candles/ingest_ticks에서 batch.tenant_id를 candle/tick에 전파

---

### F2: backfill_job 동시 실행에서 backfill↔실시간 데이터 충돌 [M]

**위치:** `backfill_job.py:158`

**문제:**
- `backfill_job.py`의 `upsert`는 `INSERT ... ON CONFLICT DO UPDATE`로 멱등성을 보장한다.
- 그러나 backfill이 실시간 수집(`ingest_candles.py`)과 동시에 같은 구간을 쓸 때:
  1. backfill이 캔들 A를 INSERT
  2. 실시간 수집이 같은 캔들 A를 INSERT → ON CONFLICT로 UPDATE (backfill 데이터 덮어씀)
  3. 또는 실시간 수집이 먼저 INSERT → backfill이 ON CONFLICT로 덮어씀
- **backfill이 실시간 데이터를 덮을 수 있음.** 백필이 "완전한 구간"을 재수집하는 목적이라면
  실시간 데이터가 백필 구간 끝에 쌓여있을 때 백필이 그 데이터를 덮어쓸 수 있다.
- `md_candle`의 PRIMARY KEY가 `(venue, instrument_id, timeframe, open_time)`이므로
  같은 open_time의 캔들은 하나만 존재 가능 — 덮어쓰기가 발생한다.

**선례 대조:**
- 원장 경로(task-8675): `SELECT ... FOR UPDATE`로 조건부 잠금
- 리스크 경로(task-8882): 실패 시 거부
- 백필: 덮어쓰기 허용 (현재 계약). 하지만 동시 실행 시 어떤 것이 "승자"인지 명시 없음.

**재현:**
```python
# backfill이 09:00-10:00 구간을 백필하는 동안
# 실시간 수집이 09:30 캔들을 삽입
# backfill이 09:30 캔들을 다시 삽입 → 실시간 데이터가 백필 데이터로 덮어씀
```

**조치 제안:**
1. backfill 실행 중 실시간 수집을 해당 구간/종목으로 차단 (lock)
2. 또는 backfill이 `ON CONFLICT` 시 `WHERE` 조건으로 실시간 데이터 보호
3. 또는 백필과 실시간 수집의 구간을 분리 (backfill은 과거, 실시간은 최근 N분)

---

### F3: ingest_ticks의 trade_id 중복 시 upsert가 기존 데이터 덮어쓰기 [M]

**위치:** `ingest_ticks.py:106`

**문제:**
- `md_tick`의 UNIQUE 제약은 `(venue, instrument_id, trade_id, traded_at)`
- `upsert`는 `ON CONFLICT (venue, instrument_id, trade_id, traded_at) DO UPDATE SET ...`
- exchange가 같은 `trade_id`를 중복 전송하는 경우(예: 네트워크 재전송),
  최신 값으로 덮어쓴다. 이는 일반적으로 의도된 동작이다.
- **그러나** `trade_id`가 exchange에서 고유하지 않고, 서로 다른 거래가 같은
  `trade_id`를 가질 수 있는 경우(드물지만 가능성), 잘못된 덮어쓰기가 발생한다.
- 현재 코드에는 "중복 trade_id를 quarantine로 분리"하는 로직이 없다.

**선례 대조:**
- 주문 경로: 중복 주문 ID는 reject (멱등 키로 확인)
- 원장: 중복 transaction_id는 reject
- 틱 수집: 중복 trade_id는 upsert (덮어쓰기) — 계약 불일치 가능성

**재현:**
```python
# 같은 trade_id, 다른 price로 두 번 전송
# 두 번째가 첫 번째를 덮어씀
```

**조치 제안:**
1. trade_id 중복 시 새 컬럼(quarantine)으로 분리
2. 또는 `traded_at`에 microsecond 정확도 추가
3. 또는 exchange 재전송 감지를 위한 `received_at` 컬럼 추가

---

### F4: default_as_of()가 datetime.now() 의존 — 결정성 문제 [M]

**위치:** `as_of_binding.py:21`

**문제:**
```python
def default_as_of() -> datetime:
    return datetime.now(UTC)
```
- `default_as_of()`는 매 호출마다 `datetime.now(UTC)`를 반환한다.
- research_data query에서 `as_of`가 제공되지 않을 때 이 함수가 호출된다.
- **테스트 결정화 문제:** 같은 입력에 대해 `default_as_of()`가 매번 다른 값을 반환하므로,
  point-in-time 조회 결과가 테스트마다 달라질 수 있다.
- CI에서 `datetime.now()`를 patch하지 않으면 비결정적 결과가 나온다.

**선례 대조:**
- eventstore replay는 `datetime.fromisoformat()`로 고정 시계 사용
- replay_verify.py도 `datetime.fromisoformat()`
- research_data query만 `datetime.now()` 직접 사용

**재현:**
```python
# 두 번 호출하면 다른 값
default_as_of() != default_as_of()  # True
```

**조치 제안:**
1. `default_as_of()`에 clock 추상화 도입 (테스트 시 patch 가능)
2. 또는 research_data query에서 `as_of`를 필수 파라미터로 변경
3. 또는 고정 시계(fixed clock)를 기본값으로 사용

---

### F5: get_candles의 as_of==close_time 경계 조건 [L]

**위치:** `get_candles.py:123`

**문제:**
```python
if as_of is not None:
    query = query.where(md_candle.close_time <= as_of)
```
- `as_of <= close_time` 조건: `as_of`보다 과거 또는 동일한 close_time을 가진 캔들만 반환.
- **경계:** `as_of == close_time`일 때 그 캔들을 포함한다.
- point-in-time 계약: "as_of 시점までに 공개된 데이터만 반환"이라면
  `close_time == as_of`인 캔들은 그 캔들이 완전히 닫힌 시점이므로 포함이 맞다.
- **그러나** `open_time == as_of`인 캔들(아직 열지 않은 캔들)은 `close_time > as_of`이므로
  자연스럽게 제외된다.
- 현재 구현은 **contract대로 보이지만** spec 문서에 명시적 경계 정의가 없다.

**재현:**
```python
# as_of가 캔들 close_time과 정확히 같은 경우
# 해당 캔들이 반환됨 (의도된 동작일 수 있음)
```

**조치 제안:**
1. spec에 `as_of == close_time` 경계 명시
2. 또는 테스트로 문서화 (현재 테스트에 경계 케이스 없음)

---

### F6: backfill_job의 partition 중복 생성 오버헤드 [L]

**위치:** `backfill_job.py:148`

**문제:**
- `create_monthly_partitions()`를 매 백필 호출 시 실행한다.
- 이미 존재하는 파티션에 대해 중복 CREATE PARTITION IF NOT EXISTS가 없으므로,
  기존 파티션은 무시되고 새 파티션만 생성된다.
- **오버헤드:** 매 백필마다 모든 달의 파티션 존재 여부를 확인하는 쿼리가 실행된다
  (내부 구현에 따라 다름).
- 짧은 구간(예: 1일)을 반복 백필할 때 불필요한 오버헤드가 누적된다.

**재현:**
```python
# 같은 1일 구간을 100번 백필 → 100번 partition 확인 쿼리
```

**조치 제안:**
1. `skip_if_exists=True` 옵션 추가
2. 또는 파티션 생성 결과를 캐시

---

### F7: replay_candles.py의 시계·난수 시드 의존 누락 [L]

**위치:** `replay_candles.py:83`

**문제:**
- `replay_candles.py`는 `eventstore.replay()`를 호출하여 기록된 이벤트를 재생한다.
- `eventstore.replay()`는 `datetime.fromisoformat()`로 고정 시계를 사용하므로
  자체적으로는 결정적이다.
- **그러나** `replay_candles.py`의 `ReplayResult` 계산에서 `datetime.now()`를
  사용하지는 않지만, caller(예: CLI)가 `datetime.now()`를 결과 로깅에 사용할 수 있다.
- **CI hash-seed 문제:** 2026-10-01 CI에서 `hash()` 시드 의존 DB 이름,
  월 1일에만 깨지는 조정 테스트가 발견됨. 이는 `replay_candles.py` 자체보다는
  테스트 코드에서 `hash()`를 캐시 키로 사용한 것이 원인.
- 제품 코드에서 `hash()` 사용: `ingest_candles.py:66` — `batch_hash = hash(json.dumps(...))`
  **이것은 실제 버그:** Python은 3.7+부터 `PYTHONHASHSEED`가 설정되지 않으면
  매 프로세스마다 `hash()` 시드를 랜덤화한다. 따라서 `batch_hash`는
  프로세스마다 달라지며, dedup이 깨질 수 있다.

**재현:**
```bash
# PYTHONHASHSEED=0 없이 실행
python ingest_candles.py --same-data  # 다른 batch_hash 생성
# 두 번째 실행이 첫 번째를 덮어쓸 수 있음
```

**조치 제안:**
1. `hash()` 대신 `hashlib.sha256()` 사용 (결정적 해시)
2. 또는 `PYTHONHASHSEED=0` 환경 변수 설정
3. `batch_hash`의 목적(DEDUP 키인가, 로깅용인가)에 따라 해결책 선택

---

## §3 선례 미적용 항목

| 계약                | 주문/원장/리스크/인증 | 데이터 수집 경로 | 불일치 |
|---------------------|----------------------|-----------------|--------|
| 명시적 tenant 바인딩 | O (tenant_id FK)     | X (md_candle/tick에 없음) | F1 |
| 조건부 쓰기/낙관적 잠금 | O (FOR UPDATE)     | X (ON CONFLICT 덮어쓰기) | F2 |
| 실패 시 거부        | O                    | X (중복 trade_id upsert) | F3 |
| 감사 이벤트          | O (audit_event FK)   | O (audit_event FK) | 일치 |

---

## §4 결론

- **발견 7건** (S: 0건, M: 4건, L: 3건)
- 가장 심각한 것은 **F1 (tenant_id 누락)** — 멀티테넌트 보안에 직접적인 영향
- 두 번째로 **F7 (hash() 시드 의존)** — 2026-10-01 CI에서 이미 확인된 부류의 결정성 문제
- 코드 변경 없음(기존 코드 감사만). 정정 리프는 CTO/PM이 발행.
