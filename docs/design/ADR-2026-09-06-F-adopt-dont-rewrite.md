# ADR-2026-09-06-F: 차용 우선 — 손으로 쓰던 리프를 자동 생성·벤더 노출로 대체

## Status
Accepted (2026-09-06, Chief Architect). 사용자 지적: "이 부분들이 구현하기 힘든가? 오픈소스·공개 모듈이 많은데 전권이 있으니 조사해서 효율적으로 차용할 수 있지 않나."

## Context
남은 격차는 지표 카탈로그와 고밀도 차트였다. 그런데 기존 명세는 그 둘을 **손으로 쓰는 방식**으로 쪼개 놨다 — 이것이 비효율의 원인이지 난이도가 아니다.
- `IND-2~6`은 "카테고리별 지표 스펙을 카테고리당 20종 이상 작성"이다. 5개 리프 × 각 280줄로 100종을 손으로 채우는 계획이었다.
- `CH-14`(멀티페인)는 이미 포크해 온 KLineChart가 **내장으로 갖고 있는 기능**을 다시 만드는 것처럼 적혀 있었다.

실측 확인(2026-09-06):
- 저장소에 **TA-Lib 0.7.1이 이미 설치**돼 있고(`pyproject.toml:21`), `talib.get_functions()`가 **161개**를 돌려준다.
- `talib.abstract.Function(name).info`가 `group`·`display_name`·`input_names`·`parameters`(기본값 포함)·`output_names`·
  **`output_flags`**를 주고, `.lookback`이 C 라이브러리의 실제 lookback을 준다.
- `output_flags`는 `Line` / `Dashed Line` / `Histogram` / `Values represent an upper limit` / `... lower limit`로,
  ADR-2026-09-06-C에서 정의한 `PlotSpec.kind`·`fill_between`에 **거의 1:1로 매핑**된다.
- 그룹 구성: Momentum 31, Overlap 18, Math Transform 15, Math Operators 11, Statistic 9, Cycle 5, Price Transform 5,
  Volatility 3, Volume 3, **Pattern Recognition 61**(캔들 패턴 — TradingView도 제공하는 기능이며 손으로는 절대 안 썼을 것).

## Decision

### D1. 지표 스펙은 손으로 쓰지 않고 TA-Lib 메타데이터에서 생성한다
- `IND-2~6`(5리프, 카테고리별 수기 작성)을 **폐기**하고 단일 리프 `IND-2g`로 대체한다:
  `talib.get_functions()` × `abstract.Function(...).info`를 순회해 `IndicatorSpec`을 생성하고,
  파라미터 범위는 기본값 기반 규칙(정수 주기 1~2000, 편차 0.1~10 등)으로 부여하며, `lookback`은 `.lookback` 실측값을 쓴다.
- **`PlotSpec` 기본값도 `output_flags`에서 자동 도출**한다(`Line`→line, `Dashed Line`→line(dashed), `Histogram`→histogram,
  upper/lower limit 쌍→band + `fill_between`). 수작업은 예외 지표만.
- 결과: 목표였던 "코어 100종"을 **161종**으로 초과 달성하고, 캔들 패턴 61종이 덤으로 들어온다.

### D2. 참조 벡터는 큐레이션하지 않고 3자 교차검증으로 만든다
- 손으로 기대값을 적는 대신 **TA-Lib C 결과 ↔ 우리 증분 엔진 ↔ 벡터 엔진** 3자를 같은 입력으로 비교한다.
  세 값이 일치하면 그 값을 참조 벡터로 고정(스냅샷)하고, 불일치는 그 지표를 등록에서 제외(fail-closed)한다.
- 이 방식이 손으로 적은 기대값보다 강하다 — C 라이브러리와의 수치 동일성까지 함께 증명된다.

### D3. 차트 고밀도 기능은 벤더 내장을 노출하는 것부터 한다
- KLineChart는 멀티페인·오버레이 시스템(`createOverlay`/`registerFigure`)·지표 레지스트리(`createIndicator`)를 내장한다.
  `CH-14`(페인)·`CH-15`(플롯 렌더러)는 **벤더 기능을 우리 `ChartEngine` 인터페이스로 노출**하는 어댑터 작업으로 재정의한다.
  자체 구현은 벤더가 못 하는 것(서버 지표 바인딩, 참조 벡터 폴백, 결정 이력 마커)에만 쓴다.
- 클라이언트 증분 계산(`CH-18`)은 **서버와 같은 C 코드를 쓰는 TA-Lib WASM 빌드를 1순위**로 한다(동일성이 구조적으로 보장됨).
  가용성이 확인되지 않으면 `technicalindicators`(MIT) 등 TS 구현을 쓰되, 참조 벡터를 통과한 지표만 클라이언트에서 그린다(기존 규칙 유지).

### D4. 원칙: "이미 있는 것을 노출/생성하고, 없는 것만 쓴다"
새 리프를 만들 때 다음 순서로 확인한다. (1) 저장소에 이미 있는가, (2) 이미 반입한 벤더가 제공하는가,
(3) 허용 라이선스 OSS가 제공하는가, (4) 메타데이터에서 생성 가능한가 — 넷 다 아니면 그때 손으로 쓴다.
이번 감사에서 확인된 중복 신설 4건과 이 수기 작성 5건은 모두 이 순서를 건너뛴 결과다.

## Consequences
- `IND-2~6`(5리프, 합계 1,400줄) → `IND-2g`(1리프, 300줄) + `IND-7g`(교차검증, 260줄). **리프 3개 감소, 지표 100 → 161종.**
- `CH-14`·`CH-15`는 규모를 낮춰 재작성(각 560/600 → 300/360).
- 남은 지표 격차의 실질 작업량이 하루치에서 **반나절 이하**로 줄어든다.
- pandas-ta(MIT, ~130종)는 TA-Lib과 상당 부분 겹치므로 IND-11에서 **중복 제외 후 순증분만** 등록한다(우선순위 하향).

## Rejected
- 카테고리별 수기 작성 유지: 같은 결과를 5배 비용으로 얻는다.
- 벤더 기능을 우회한 자체 페인·오버레이 구현: 포크한 이유(기간 단축)를 스스로 무효화한다.
- GPL/LGPL 지표 라이브러리(tulip 등) 차용: 라이선스 금지 유지.
