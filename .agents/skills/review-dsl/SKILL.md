---
name: review-dsl
description: AIOS Script(DSL) — 결정론, look-ahead 차단, 리소스 상한, 컴파일 검증 축 검토 체크리스트
paths:
  - "src/core/script/**"
  - "src/core/strategy/**"
  - "src/core/indicators/**"
  - "script/**"
tier: [M]
axis: dsl
---

# review-dsl

## 적용 조건

DSL 파서/컴파일러/런타임(`script/`), 전략 신호 평가(`src/core/strategy/**`, FROZEN_PAPER_ONLY —
decision 확인 없이 수정 금지), 지표 레지스트리(`src/core/indicators/**`)를 건드릴 때 적용한다.
`docs/specs/L4_strategy_portfolio_backtest_v1.0.md` §1(R1·R3), `L4_analytics_authoring_
backtest_marketplace_v1.0.md`가 규범이다.

## 체크리스트

1. 같은 전략 아티팩트 + 같은 입력 스냅샷 + 같은 정책 버전이면 신호·배분·백테스트 결과
   해시가 바이트 단위로 동일하다(결정론) — 실행 순서가 딕셔너리 순회, `set` 순서, 시스템
   시계, 스레드 스케줄링 등 비결정 요소에 의존하지 않는다
   [근거: spec:L4_strategy_portfolio_backtest_v1.0.md#R1] [검사: 후보]
2. 신호 시점에서 미래 bar·미래 상장 정보를 참조할 수 없음이 코드로 강제된다 — look-ahead는
   경고가 아니라 컴파일/런타임 hard fail이다
   [근거: spec:L4_strategy_portfolio_backtest_v1.0.md#R3] [검사: 후보]
3. `research.*` 등 외부 데이터 조회 함수는 `as_of`를 자동 바인딩하고, 미래 시점 데이터
   요청은 컴파일/런타임에서 거부된다(누수 테스트 존재) [근거: RD-9] [검사: 후보]
4. 스크립트 무한/과다 계산은 컴파일 시점 산정(정적 상한)과 런타임 카운터(동적 상한)로
   이중 방어되고, 상한 초과 시 거부/중단 후 사용자에게 수정 가능한 오류를 반환한다(무음
   실패·무한 대기가 없다) [근거: spec:L4_analytics_authoring_backtest_marketplace_v1.0.md#script.resource_limit]
   [검사: 후보]
5. `ta.*` 내장 함수는 레지스트리 버전이 고정되고, 버전 변경 시 기존 컴파일 산출물의 해시가
   달라진다(암묵적으로 같은 이름 다른 동작이 되지 않는다)
   [근거: spec:L4_analytics_authoring_backtest_marketplace_v1.0.md#DSL-9] [검사: 후보]
6. 미지 지표·미등록 함수 호출은 컴파일 단계에서 명시적으로 거부되고, 알 수 없는 대체값
   (0, NaN 무시 등)으로 조용히 넘어가지 않는다
   [근거: spec:L4_strategy_portfolio_backtest_v1.0.md#L07] [검사: 후보]
7. 스크립트 라이브러리 import(`import lib.name@version`)는 순환 참조와 버전 불일치를
   거부한다 [근거: spec:L4_analytics_authoring_backtest_marketplace_v1.0.md#DSL-16]
   [검사: 후보]
8. Pine 등 외부 언어 변환기는 미지원 구문을 조용히 무시하지 않고 위치 정보를 포함해
   거부한다(변환 후 의미가 달라지는 구문을 "일단 통과"시키지 않는다)
   [근거: spec:L4_analytics_authoring_backtest_marketplace_v1.0.md#DSL-14] [검사: 후보]
9. 백테스트와 라이브가 같은 컴파일 산출물·같은 도메인 로직(주문/포지션/비용)을 공유한다 —
   백테스트 전용으로 별도 구현된 신호/체결 계산 경로가 없다
   [근거: I-05] [검사: 후보]
10. `event_loop.replay`의 bar 처리 순서가 고정된 결정론 순서를 따른다(동시 신호 발생 시
    처리 순서가 매 실행마다 달라지지 않는다) [근거: spec:L4_strategy_portfolio_backtest_v1.0.md#2.4]
    [검사: 후보]
11. walk-forward/파라미터 그리드 탐색에서 동률 처리 규칙(예: IS net Sharpe 최대, 동률 시
    파라미터 사전순)이 코드에 고정되어 있다(구현마다 다른 tie-break를 쓰지 않는다)
    [근거: spec:L4_strategy_portfolio_backtest_v1.0.md#2.4] [검사: 후보]
12. Decimal 정밀도가 신호·사이징 계산 전체에서 일관된다(중간 계산에서 `float`로 캐스팅해
    반올림 오차를 누적하지 않는다) [근거: 11_implementation_rules_v1.2.md]
    [검사: scripts/check_consistency.py::check_money_float]

## 반례

### 반례 1 — 다음 bar를 미리 참조하는 look-ahead

```python
# BAD: 현재 bar 인덱스보다 미래의 종가를 조건에 사용한다.
def evaluate(bars: list[Candle], i: int) -> bool:
    return bars[i + 1].close > bars[i].close  # 미래 bar 참조

# GOOD: 현재 bar까지만 참조하고, close_time <= as_of를 강제.
def evaluate(bars: list[Candle], i: int) -> bool:
    return bars[i].close > bars[i - 1].close
```

### 반례 2 — 리소스 상한 없는 사용자 스크립트 루프

```python
# BAD: 사용자가 만든 스크립트의 반복문 상한이 없어 무한/과다 계산이 프로세스를 먹는다.
def run_user_script(ast: ScriptAST, bars: list[Candle]) -> SignalSeries:
    for node in ast.walk():
        node.execute(bars)  # 카운터 없음

# GOOD: 컴파일 시점 정적 상한 산정 + 런타임 카운터로 이중 방어.
def run_user_script(ast: ScriptAST, bars: list[Candle]) -> SignalSeries:
    budget = estimate_cost(ast, len(bars))  # 컴파일 시점 산정
    if budget > MAX_SCRIPT_COST:
        raise ScriptResourceLimitError(budget, MAX_SCRIPT_COST)
    counter = RuntimeCounter(limit=MAX_RUNTIME_OPS)
    for node in ast.walk():
        counter.tick()
        node.execute(bars)
```

## 이 축에서 났던 사고

- `lookback.py`가 tf별 `required_bars`를 계산하면서 미지 지표를 조용히 통과시키지 않고
  명시적으로 거부하도록 구현 (git:e7f5d49d)
- `state_memory`/`market_state` 계약을 신설하면서 look-ahead 차단(미래 상태 참조 금지)을
  코드로 강제 (git:7c1dbb57)
- FA 계열 감사에서 반복 지적된 "구현됨≠배선됨" 패턴과 동일하게, DSL 리프들의 D-등급 재대조
  (`DEPTH_DSL_IND.md`)에서 negative test·실패주입 부족이 다수 발견돼 DEEPEN 재작업이
  전수 발행됨 (git:4ae86abc)
