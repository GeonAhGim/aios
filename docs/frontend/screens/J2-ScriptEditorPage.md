# J2-ScriptEditorPage — `/scripts/editor`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ScriptEditorPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/scripts/editor` / `frontend/apps/web/src/routes/scripts/ScriptEditorPage.tsx`
- 여정 소속: J2


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J2-ScriptEditorPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P2(전략 생산자) — "전략·스크립트를 만들고 백테스트·검증" (§2 P2 행, "스크립트 편집기·전략 빌더·백테스트·AI 스튜디오 화면 [확인]") |
| 목적(한 문장) | DSL 스크립트를 컴파일해 해시·명령 수를 확인하고, 같은 스크립트로 빠른 백테스트를 돌리거나 차트로 넘긴다 (`ScriptEditorPage.tsx:157-161`, `:189-199`, `:133-141`) |
| 진입 조건 | `frontend/apps/web/src/router.tsx:118` `{ path: "/scripts/editor", element: protect(<ScriptEditorPage />) }` — `protect`(:70-72)는 `<ProtectedRoute>`로 감싸 미로그인 시 리다이렉트. 메뉴 진입점 `frontend/apps/web/src/components/layout/navItems.ts:24`. 라우트 자체에 플래그 조건 없음. ProtectedRoute 세부 동작 미확인 |
| 사용자 준비물 | (a) 로그인된 세션(`router.tsx:118` protect), (b) DSL 소스 — 기본 예제 `ScriptEditorPage.tsx:33-40`에 미리 들어 있음, (c) 백테스트 시 URL `instrument_id` 파라미터(`:160`, 없으면 `:163` 오류), 최근 7일 구간(`:165`) — 해당 종목 시장 데이터 존재 여부 미확인 |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J2-ScriptEditorPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `compileScript` (`useMutation`, `ScriptEditorPage`) | `apiClient.compileScript` (`ScriptEditorPage.tsx:157`) | 사용자 클릭(컴파일 버튼, `:244`) 1회 요청. 자동 재조회/폴링 없음. | `ApiError`且`isScriptCompileErrorDetails` → `ScriptEditor` 마커로 단일 오류 표시(`:221-223`). 그 외 오류 → `ErrorMessage`(`:248-254`)에 `traceId`·`errorCode`·`message` 전달. |
| `backtest` (`useMutation`, `CompilePreview`) | `backtestsClient.runQuickBacktest` (`:166`) | 컴파일 성공 후 "백테스트 실행" 버튼(`:138`) 1회 요청. | `backtest.isError`(`:144`) → `role="alert"` div(`:145`)에 `getApiErrorMessage` 결과 표시. 성공 시 `BacktestSummary`(`:152`). |
| `useSearchParams` (URL `instrument_id`) | `react-router-dom` (`:159`) | URL 변경 시 자동 갱신. | 파라미터 없으면 `runBacktest`가 `throw new Error`(`:163`). |
| `source` (`useState`) | 로컬 상태 (`:188`) | `ScriptEditor` `onChange`(`:227`)로 매 키스트roke 갱신. 초기값 `DEFAULT_SOURCE`(`:33-40`). | 편집 시 `mutation.reset()`(`:229`)로 이전 상태 클리어. |
| `preview` (`useState` + `useEffect`) | `chart-engine` scriptPreview (`:189-190`, `:204-211`) | `mutation.data` 변경 시 `useEffect`에서 `syncScriptPreview`(`:207`)로 자동 갱신. | 동기 함수라 throw 없음. |
| `useTranslation` (`react-i18next`) | `react-i18next` (`:18`) | 앱 시작 시 1회 로드. | i18n 키 누락 시 키 문자열 자체 표시(기본 동작). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J2-ScriptEditorPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | 컴파일 요청 중(`mutation.isPending`, `ScriptEditorPage.tsx:242`·`:244`) 또는 백테스트 요청 중(`backtest.isPending`, `:139`) | 컴파일 버튼 "컴파일 중..."(`:245`, 하드코딩 문자열), 백테스트 버튼 `scriptEditorPage.backtestRunning` "백테스트 실행 중..."(`:141`) | 편집기·버튼 비활성화로 대기(`:242` disabled, `:139` disabled, `:244` disabled) | 구현됨(`:139`, `:141`, `:242`, `:244-245`) |
| 빈 상태 | 소스가 공백(`source.trim().length === 0`, `:244`) → 컴파일 버튼 비활성. 미리보기 패널은 등록된 플롯이 없으면 `null`(`:72`). 편집기 초기값은 `DEFAULT_SOURCE`(`:33-40`, `:188`) | 빈 상태 안내 문구 없음(사실 시트 문구 목록에도 없음) | 소스 입력 | 비활성화 구현됨(`:244`), 안내 문구 없음 |
| 정상 | `mutation.isSuccess`(`:256`) → `CompilePreview` 렌더 | 해시 `legacy.scriptEditorPage.t3`(`:118`), 명령 수·시리즈 수·연산 수·플롯 수·컴파일 시간 `t4`~`t8`(`:121-130`), "차트에서 백테스트" 버튼(`:134`, 하드코딩), 백테스트 결과 `legacy.backtestPanel.t8`~`t10`(`:91-96`, 성공 시 `:152`), 미리보기 패널 `t1`·`t2`(`:75`, `:79`, 플롯 있을 때 `:72`) | 차트 이동(`handleOpenChart`, `:197-198`), 백테스트 실행(`:138`), 편집(`:227`) | 구현됨(`:118`, `:121-130`, `:134`, `:138`, `:152`, `:256`) |
| 낡은 데이터(신선도 초과) | 신선도 기준·타임스탬프 로직은 소스에 없다. 실질적 "낡음"은 편집 전 이전 컴파일 결과가 남아 있는 경우 | 편집 시 결과 즉시 제거(`mutation.reset()`, `:229`)와 미리보기 정리(`:230-234`) → 낡은 결과 화면에 남지 않음. 생성 시각·백테스트 구간(`now-7일`, `:164-165`)은 화면 미표시 | 재컴파일(`:244`) | 낡은 결과 제거 구현됨(`:227-234`), 신선도 표시·시각 표기 없음 |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J2-ScriptEditorPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `compileScript` mutation 실패, 응답 `details`가 `ScriptCompileErrorDetails` 스키마 (`isScriptCompileErrorDetails`, `shared-types`) → `ScriptEditor` 마커로 단일 오류 표시 (`ScriptEditorPage.tsx:213-223`) | 컴파일 에러 라인/컬럼에 빨간 불빛 + 오류 코드 메시지 (`legacy.scriptEditorPage.t3` 아래, 마커로만 표시, 별도 문구 키 없음) | 스크립트 수정 후 재컴파일 | 구현됨 (`:213-223`) |
| 인증 필요/세션 만료(401) | `compileScript` mutation 실패, 응답이 `ApiError`이지만 `isScriptCompileErrorDetails` 아님 → `showGenericError=true` → `ErrorMessage` (`:225`, `:248-254`)에 `errorCode`, `message`, `traceId` 전달 | `ErrorMessage` 컴포넌트 (i18n 키 없음, 기존 `routeApiError` 매핑 경로 재사용, `:23-24` 주석) | 로그인 재시도 | 구현됨 (`:225`, `:248-254`) |
| 권한 없음/MFA 필요(403) | 401과 동일 경로 (`ErrorMessage`, `:225`, `:248-254`) | `ErrorMessage` 컴포넌트 | 권한 요청 | 구현됨 (`:248-254`) |
| 충돌/상태 불일치(409) | 401과 동일 경로 (`ErrorMessage`, `:225`, `:248-254`) | `ErrorMessage` 컴포넌트 | 재시도 | 구현됨 (`:248-254`) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J2-ScriptEditorPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | `compileScript` mutation 실패, 응답 상태 429 → `isScriptCompileErrorDetails` 아님 → `showGenericError=true` → `ErrorMessage`(`:225`, `:248-254`). `backtest` mutation 실패, 상태 429 → `backtest.isError`(`:144`) → alert div(`:145-150`). | `ErrorMessage` 컴포넌트(compile), alert div에 오류 메시지(backtest). 속도 제한 전용 문구·카운트다운·재시도 대기 UI 없음. | 응답 메시지에 재시도 시간 포함 시 사용자 수동 재시도 | 구현됨(공통 오류 경로 재사용, `:225`, `:248-254`, `:144-150`), 전용 UI 없음 |
| 서버 오류(5xx) | `compileScript` mutation 실패, 응답 상태 5xx → `showGenericError=true` → `ErrorMessage`(`:225`, `:248-254`). `backtest` mutation 실패, 상태 5xx → `backtest.isError`(`:144`) → alert div(`:145-150`). | `ErrorMessage` 컴포넌트(compile), alert div에 `getApiErrorMessage` 결과(backtest). 5xx 전용 재시도·대기 UI 없음. | 수동 재컴파일/재실행 | 구현됨(공통 오류 경로 재사용, `:225`, `:248-254`, `:144-150`), 전용 UI 없음 |
| 네트워크 단절 | `compileScript`/`backtest` 요청 중 네트워크 오류 → react-query mutation이 `isError`로 전환. `ApiError` 아님 → `showGenericError=true`(`:225`) 또는 `backtest.isError`(`:144`). | compile: `ErrorMessage`에 `mutation.error.message`(네트워크 오류 문구). backtest: alert div에 `backtest.error.message`. react-query mutation 기본값: 자동 재시도 없음. | 수동 재시도 | 구현됨(공통 오류 경로, `:225`, `:248-254`, `:144-150`), 자동 재시도 없음 |
| 부분 실패(일부 데이터만 실패) | 컴파일 성공(`mutation.isSuccess`, `:256`) → `CompilePreview` 렌더, 그 안에서 `backtest` mutation 실패(`:144`) → 백테스트 오류 표시. 컴파일 실패 → 백테스트 불가. | 컴파일 결과(해시·명령 수 등)는 표시되나 백테스트 결과 없음. backtest error div(`:144-151`)에 실패 사유. | 컴파일 재시도 또는 백테스트 재실행 | 구현됨(두 mutation 독립 실행, `:256`, `:144-151`), 명시적 "부분 실패" 안내 없음 |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J2-ScriptEditorPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/scripts/ScriptEditorPage.tsx:133`) | 컴파일 성공 후 `CompilePreview` 렌더링 중 (`mutation.isSuccess`, `:256`) | `/chart?script_hash=<hash>` 페이지로 이동 — `handleOpenChart`(`:197-199`) 호출, `navigate`(`:187`) 사용 | 없음 (router.push 실패 미확인) | `/chart` 페이지 (`router.tsx:122` 차트 라우트 미확인) |
| `<Button>` (`frontend/apps/web/src/routes/scripts/ScriptEditorPage.tsx:136`) | 컴파일 성공 후 `CompilePreview` 렌더링 중, `backtest.isPending`이 아님 (`:139` disabled 조건) | `backtest.mutate(source)`(`:138`) → `runBacktest(source)`(`:166`) → `backtestsClient.runQuickBacktest(venue, instrumentId, timeframe, start, end, initialCash, scriptSource, config)` — 성공 시 `BacktestSummary`(`:152`)에 finalEquity/fills/bars 표시 | `instrument_id` 없으면 `throw new Error(t("scriptEditorPage.backtestNeedsInstrument"))`(`:163`). mutation 실패 시 `role="alert"` div(`:145-150`)에 `getApiErrorMessage` 결과 표시 | 같은 페이지 (`ScriptEditorPage`, `:144-152`) |
| `<Button>` (`frontend/apps/web/src/routes/scripts/ScriptEditorPage.tsx:244`) | `source.trim().length > 0`, `mutation.isPending`이 아님 (`:244` disabled 조건) | `compileScript(source)`(`:244` onClick) → `useMutation`(`:189`) POST `/v1/scripts/compile`. 성공 시 `CompilePreview`(`:257-263`) 렌더 — 해시·명령수·시리즈수·연산수·플롯수·컴파일시간 표시, `ScriptPreviewPanes`(`:264`) 갱신 (`:204-211` useEffect) | `isScriptCompileErrorDetails` 응답(`:213-216`) → `ScriptEditor` marker(`:221-223`)에 단일 오류 표시(`:221-223`). 그 외 `ApiError` → `ErrorMessage`(`:249-253`)에 errorCode/message/traceId 전달 | 같은 페이지 (`ScriptEditorPage`, `:256-264`) |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J2-ScriptEditorPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J2-ScriptEditorPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `Alert` (`frontend/apps/web/src/routes/scripts/ScriptEditorPage.tsx:116`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/scripts/ScriptEditorPage.tsx:249`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J2-ScriptEditorPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J2-ScriptEditorPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J2-ScriptEditorPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
