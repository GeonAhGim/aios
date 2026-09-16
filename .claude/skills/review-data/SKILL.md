---
name: review-data
description: 시장데이터 point-in-time, tz-aware, 갭/정체 탐지, 캘린더, 커버리지 fail-closed 축 검토 체크리스트
paths:
  - "src/foundation/market_data/**"
  - "src/data/**"
  - "src/core/observability/**"
tier: [M]
axis: data
---

# review-data

## 적용 조건

시장데이터 수집/저장/조회(`src/foundation/market_data/`), 갭·정체 탐지, 캘린더·세션 규칙을
건드릴 때 적용한다. `docs/specs/L4_market_data_positions_ledger_v1.0.md` §2.2·§4.1,
`docs/specs/L4_strategy_portfolio_backtest_v1.0.md`(point_in_time.py)가 규범이다.

## 체크리스트

1. 모든 캔들의 `open_time`/`close_time`/저장 타임스탬프가 tz-aware UTC다 — naive
   datetime이 저장·조회 경계를 넘지 않는다
   [근거: 01_data_models_v1.4.md §1.7] [검사: scripts/check_consistency.py::check_naive_datetime]
2. 단일 캔들 정합성(`low<=min(open,close)`, `high>=max(open,close)`, `volume>=0`,
   `close_time==open_time+duration`)이 저장 전에 검증된다
   [근거: spec:L4_market_data_positions_ledger_v1.0.md#2.2] [검사: 후보]
3. 데이터 커버리지 갭이 있는 구간을 조회하면 있는 데이터만 반환하며 "정상"으로 위장하지
   않는다 — 갭 존재 자체가 fail-closed 판정(`DATA_COVERAGE_MISSING`류) 대상이다
   [근거: DC-7] [검사: 후보]
4. 갭/정체 탐지가 venue 축을 함께 검증한다 — 한 venue의 정상 데이터로 다른 venue의 결측을
   가리는(fail-open) 경로가 없다 [근거: DC-7, git:55b597b5] [검사: 후보]
5. point-in-time 조회(`series_cache` 등)가 `as_of` 이후 시점의 데이터를 반환하지 않는다 —
   전구간 1회 계산 캐시라도 조회 시점 필터링을 생략하지 않는다
   [근거: spec:L4_strategy_portfolio_backtest_v1.0.md#2.5] [검사: 후보]
6. bar 시각이 단조증가하고, `close_time <= as_of`, source lineage(출처 계보)가 존재한다 —
   lineage 없는 데이터는 hard fail이다 [근거: spec:L4_strategy_portfolio_backtest_v1.0.md#point_in_time.py]
   [검사: 후보]
7. venue별 세션 규칙(정규장·조기폐장·24×7, 휴장일)이 하드코딩된 고정 캘린더가 아니라
   입력으로 받는 순수 함수로 계산된다(새 휴장일 발생 시 코드 배포 없이 갱신 가능)
   [근거: spec:L4_market_data_positions_ledger_v1.0.md#2.2] [검사: 후보]
8. 기업행위(corporate action) 기록이 `(instrument, type, ex_date)` 멱등키로 중복 기록을
   거부한다 [근거: spec:L4_market_data_positions_ledger_v1.0.md#2.2] [검사: 후보]
9. 백필(backfill) 작업이 plan-fetch-store-coverage 단계를 거치고, 중간 실패 시 이미 저장된
   구간과 미저장 구간이 커버리지 메타데이터에 정확히 반영된다(부분 실패를 "완료"로 표시
   하지 않는다) [근거: DC-16] [검사: 후보]
10. 데이터 소스 신뢰도·라이선스 제약이 코드에 검증 없이 가정되지 않는다(§10 R2류 미확정
    외부 사실은 "미검증"으로 docstring에 남는다) [근거: FULL_AUDIT_2026-09-02.md]
    [검사: 후보]
11. 동시에 여러 워커가 같은 캔들 구간을 백필해도 중복 삽입 없이 UPSERT/멱등 처리된다
    [근거: DC-7] [검사: 후보]
12. 관측 지표(`data_delay_sec` 등 freshness 트래커)가 데이터 계층에서 미주입 시 `0`이
    아니라 `None`(모름)으로 전파되어 다운스트림(Circuit Breaker 등)이 결손을 정상으로
    읽지 않는다 [근거: RTF-02] [검사: 후보]

## 반례

### 반례 1 — 갭이 있는 구간을 조용히 부분 반환

```python
# BAD: 요청 구간에 갭이 있어도 있는 데이터만 반환 -- 호출부가 "완전한 데이터"로 오인.
async def get_candles(symbol: str, start: datetime, end: datetime) -> list[Candle]:
    return await self._repo.fetch(symbol, start, end)  # 갭 검사 없음

# GOOD: 갭이 있으면 커버리지 상태를 함께 반환하고, 임계 초과 시 fail-closed.
async def get_candles(symbol: str, start: datetime, end: datetime) -> CandleResult:
    candles = await self._repo.fetch(symbol, start, end)
    gaps = detect_gaps(candles, start, end)
    if gaps.exceeds_threshold():
        raise DataCoverageMissingError(symbol, gaps)
    return CandleResult(candles=candles, gaps=gaps)
```

### 반례 2 — point-in-time 캐시가 미래 데이터를 누출

```python
# BAD: 전구간을 미리 계산해 캐시하고 as_of 필터링 없이 그대로 반환한다.
def get_indicator(symbol: str, as_of: datetime) -> Decimal:
    return self._cache[symbol][-1]  # 캐시의 마지막 값 = 미래 포함 가능

# GOOD: as_of 이하 시점까지만 필터링한다.
def get_indicator(symbol: str, as_of: datetime) -> Decimal:
    series = self._cache[symbol]
    visible = [p for p in series if p.close_time <= as_of]
    if not visible:
        raise PointInTimeDataMissingError(symbol, as_of)
    return visible[-1].value
```

## 이 축에서 났던 사고

- `gaps.py`가 venue 축을 검증하지 않아 한 venue 결측을 다른 venue 데이터로 가리는
  fail-open이 있었던 결함을 보정 (git:55b597b5)
- 커버리지 갭 판정을 fail-open에서 fail-closed로 전환하는 `domain/coverage/gaps.py`를
  신설 (git:b1dbedc9)
- Circuit Breaker `data_delay_sec`가 freshness 트래커 미주입 시 상수 0으로 고정돼 stale
  데이터를 정상으로 읽던 결함(데이터 계층 신호가 안전 계층까지 오염) (git:a0652c9)
