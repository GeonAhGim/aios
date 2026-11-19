# J1-DemoChartPage — `/onboarding/demo/:instrumentId`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/DemoChartPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/onboarding/demo/:instrumentId` / `frontend/apps/web/src/routes/onboarding/DemoChartPage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-DemoChartPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1(개인 트레이더). 근거: PERSONAS_AND_JOURNEYS.md:22 — "P1 | **개인 트레이더(투자자)** | 전략을 만들거나 고르고, 검증하고, 모의->실운용하며 감독 | J1~J6 전체 [확인]". :40 — "시작·온보딩(J1) | P1, 이후 모든 페르소나가 **자기 버전**을 가진다". 데모 모드는 J1 온보딩 여정의 2단계로 P1 전용. |
| 목적(한 문장) | 데모 모드에서 샘플 종목의 일일 봉 차트를 보고, 버튼 하나로 백테스트를 실행해 결과(최종 자산, 거래 횟수, 최대 낙폭)를 확인한다. 근거: DemoChartPage.tsx:62-63(차트 캔버스 `data-testid="demo-chart-canvas"`), :66-68(백테스트 실행 버튼 `handleRunBacktest`), :72-83(결과 섹션 `demo-backtest-summary`). |
| 진입 조건 | 로그인 필요: `router.tsx:108`에서 `protect(...)` → `ProtectedRoute` 가드. :109-110에서 `FeatureFlagGate flag="onboarding_demo_mode"`로 데모 모드 기능 플래그가 꺼지면 `FeatureFlagGate.tsx:17`의 `common.featureDisabled` 문구 표시. :107에서 `/onboarding/demo/:instrumentId` 라우트 파라미터 `instrumentId` 필요. `DemoChartPage.tsx:37-38`에서 `findDemoInstrument(instrumentId)`가 `null`이면 알 수 없는 종목 에러 페이지(`DemoChartPage.tsx:51`) 표시. |
| 사용자 준비물 | 데모 모드 진입점(`/onboarding/demo`)에서 종목 선택 완료: DemoModePage.tsx:29(`Link to=/onboarding/demo/${instrument.id}`). 데모 모드는 실제 계좌 연결이 필요 없으므로 추가 준비물 없음. |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J1-DemoChartPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `findDemoInstrument(instrumentId)` | `demoDataset.ts:52` | 1회 조회(컴포넌트 마운트 시 `DemoChartPage.tsx:21`). 동기 함수, `DEMO_INSTRUMENTS` 배열에서 `find()` 탐색. 폴링/반복 갱신 없음. | `undefined` → `DemoChartPage.tsx:37`에서 `!instrument` 조건 참, `EmptyState` + "알 수 없는 데모 종목입니다."(`demoMode.chart.unknownInstrument`) 표시. `Link to=/onboarding/demo` 돌아가기 버튼 제공. |
| `getDemoDailyCandles(instrumentId)` | `demoDataset.ts:59` | 1회 조회(`DemoChartPage.tsx:22`, `useMemo` 메모이제이션). mulberry32 PRNG로 365일 일봉 계산 후 `dailyCandleCache`(Map) 캐시. 두 번째 호출부터 캐시 히트(`demoDataset.ts:60-61`). 폴링 없음. | instrumentId 미등록 시 빈 배열(`[]`) 반환(`demoDataset.ts:62`). 차트에 캔들 없음으로 표시, 별도 오류 UI 없음. |
| `runDemoBacktest(bars)` | `demoBacktest.ts:28` | 변경 요청(버튼 클릭 `handleRunBacktest`, `DemoChartPage.tsx:28-35`). `setTimeout(0)`으로 한 틱 미룸(`DemoChartPage.tsx:31-34`). 순수 함수. | 빈 배열 시 초기 자산 동일 결과(`demoBacktest.ts:29-37`). 예외 던짐 없음. `isRunning=true` 시 `LoadingState` 표시(`DemoChartPage.tsx:70`). |
| `useState` (`isRunning`, `result`) | `DemoChartPage.tsx:25-26` | 로컬 상태. `handleRunBacktest` 호출 시 `isRunning=true`→완료 시 `result=DemoBacktestResult`, `isRunning=false`. | `runDemoBacktest` 순수 동기 함수이므로 예외 발생 적음. `isRunning`이 `true`에 고정되면 `LoadingState`에 정지. |
| `useTranslation()` | `react-i18next` (`DemoChartPage.tsx:20`) | 1회 호출. i18n 키→문자열 매핑. `react-i18next` 내부 캐시. | 번역 키 부재 시 키 자체 표시(예: `demoMode.chart.pageTitle`). 오류 스택 트레이스 없음. |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-DemoChartPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J1-DemoChartPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-DemoChartPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-DemoChartPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/onboarding/DemoChartPage.tsx:45`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/onboarding/DemoChartPage.tsx:66`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-DemoChartPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-DemoChartPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| TODO | | |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-DemoChartPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-DemoChartPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-DemoChartPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
