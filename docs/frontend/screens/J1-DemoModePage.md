# J1-DemoModePage — `/onboarding/demo`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/DemoModePage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/onboarding/demo` / `frontend/apps/web/src/routes/onboarding/DemoModePage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-DemoModePage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더) — 온보딩(J1) 여정의 첫 화면, "첫 백테스트까지 5분" DoD의 1단계(task-2632, `DemoModePage.tsx:7~8`) |
| 목적(한 문장) | 고정 샘플 데이터(1년치 1분봉, 3종목)로 실제 연결 없이 차트와 백테스트를 체험하게 한다 (`DemoModePage.tsx:16`, `DemoModePage.facts.md:13`) |
| 진입 조건 | 로그인(`ProtectedRoute`, `router.tsx:70~72`) + `onboarding_demo_mode` FeatureFlag 활성화(`router.tsx:101`) |
| 사용자 준비물 | 없음 — 데모 모드는 실제 거래소 연결·위임장·계정 설정 없이 바로 체험 가능 (`DemoModePage.tsx:16`) |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J1-DemoModePage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useTranslation()` (i18n 문자열) | `react-i18next` (`DemoModePage.tsx:10`) | 앱 부트스트랩 시 1회 로드, 언어 변경 시 재호출 | i18n 키가 누락되면 키 문자 그대로 표시(react-i18next 기본), 화면 렌더 차단 없음 |
| `DEMO_INSTRUMENTS` (샘플 종목 목록) | `./demoDataset.ts:13~17` (`DemoModePage.tsx:5`) | 모듈 로드시 1회 평가, readonly 상수 — 갱신 없음 | 상수 배열이므로 실패 없음; 빈 배열이면 종목 목록이 안 보임 |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-DemoModePage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | 컴포넌트에 로딩 분기 없음(`DemoModePage.tsx:9~41`에 조건부 렌더링 부재). 데이터는 모듈 상수(`demoDataset.ts:13~17`). i18n 지연 로드 중 동작 | 없음 | 없음 | 없음(근거: 로딩 분기 부재 `DemoModePage.tsx:9~41`). i18n 지연 로드 중 동작은 `미확인` |
| 빈 상태 | `DEMO_INSTRUMENTS`는 길이 3의 고정 배열(`demoDataset.ts:13~17`). 빈 배열일 때 안내 문구 없음. 사실 시트에 빈 상태 문구 키 없음(사실 시트 10~16줄) | 빈 `<ul>`만 남음 | 없음 | 없음(근거: 고정 길이 3 배열, 빈 상태 처리 부재 `DemoModePage.tsx:19~36`) |
| 정상 | 페이지 렌더링. `DEMO_INSTRUMENTS`의 3개 종목 렌더링 | 제목 `demoMode.pageTitle`("데모 모드", `DemoModePage.tsx:15`), 설명 `demoMode.description`(`:16`), "샘플 종목" 라벨 `demoMode.instrumentListLabel`(`:18`), 종목별 "(샘플)" 태그 `demoMode.sampleTag`(`:27`), "데모 시작" 버튼 `demoMode.startCta`(`:31`) | 종목별 "데모 시작" 버튼 클릭 시 `/onboarding/demo/{id}`로 이동 (`DemoModePage.tsx:29~32`), 테스트 ID `demo-start-{id}`(사실 시트 44줄) | 구현됨(`DemoModePage.tsx:15~32`, 사실 시트 12~16줄) |
| 낡은 데이터(신선도 초과) | 페이지는 `Date.now()`를 쓰지 않음. 데이터셋은 고정 원점 `2025-01-01 UTC`로 결정론적 생성(`demoDataset.ts:21~23`). "낡음" 조건·문구 없음 | 없음 | 없음 | 없음(근거: 신선도 검사 부재 `DemoModePage.tsx:9~41`). 앱 전체 신선도 처리는 `미확인` |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J1-DemoModePage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | 이 화면에 입력 폼 없음(`DemoModePage.tsx:9~41`에 폼 요소·제어 상태 부재). 사실 시트 `Form fields` 표 공백(`DemoModePage.facts.md:18~21`) | 없음 | 없음 | 없음(근거: 입력 폼 부재 `DemoModePage.tsx:9~41`, 사실 시트 `Form fields` 공백 `DemoModePage.facts.md:18~21`) |
| 인증 필요/세션 만료(401) | 이 화면에 API 호출 없음(`DemoModePage.tsx:9~41`에 fetch/axios/React Query 부재). 사실 시트 `Error handling calls` 표 공백(`DemoModePage.facts.md:35~38`). 라우트 가드 `ProtectedRoute`(router.tsx:70~72)가 401 처리 여부를 이 파일에서 확인 불가 | 없음 | 없음 | 없음(근거: API 호출 부재 `DemoModePage.tsx:9~41`, 사실 시트 `Error handling calls` 공백 `DemoModePage.facts.md:35~38`). 라우트 가드 401 처리 여부: 미확인(`router.tsx:70~72`는 이 파일에서 확인 불가) |
| 권한 없음/MFA 필요(403) | 이 화면에 권한 체크 API 호출 없음(`DemoModePage.tsx:9~41`). 사실 시트 `Error handling calls` 표 공백(`DemoModePage.facts.md:35~38`) | 없음 | 없음 | 없음(근거: 권한 체크 호출 부재 `DemoModePage.tsx:9~41`, 사실 시트 `Error handling calls` 공백 `DemoModePage.facts.md:35~38`) |
| 충돌/상태 불일치(409) | 이 화면에 상태 변경 API 호출 없음(`DemoModePage.tsx:9~41`). 사실 시트 `Error handling calls` 표 공백(`DemoModePage.facts.md:35~38`) | 없음 | 없음 | 없음(근거: 상태 변경 호출 부재 `DemoModePage.tsx:9~41`, 사실 시트 `Error handling calls` 공백 `DemoModePage.facts.md:35~38`) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-DemoModePage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 이 화면에 HTTP 호출이 없다(`DemoModePage.tsx:9~41`에 fetch/axios/React Query 부재). 423/429가 발생할 경로가 없다. `router.tsx:101`의 `onboarding_demo_mode` 플래그는 FeatureFlag(로컬 localStorage, `featureFlags.ts:10~15`)이므로 HTTP 잠김이 아님. 플래그 미활성 시 `FeatureFlagGate`가 자식을 렌더링하지 않음(`FeatureFlagGate.test.tsx:25~28`) — 이 파일에서 확인 불가 | 없음 | 없음 | 없음(근거: HTTP 호출 부재 `DemoModePage.tsx:9~41`). 플래그 미활성 시 동작: 미확인(`router.tsx:101`, `FeatureFlagGate` 구현은 이 파일에서 확인 불가) |
| 서버 오류(5xx) | HTTP 호출이 없으므로 5xx 발생 경로가 없다(`DemoModePage.tsx:9~41`). 종목 목록은 `./demoDataset.ts:13~17`의 모듈 상수(`DEMO_INSTRUMENTS`)이므로 서버 응답에 의존하지 않음 | 없음 | 없음 | 없음(근거: HTTP 호출 부재 `DemoModePage.tsx:9~41`, `DEMO_INSTRUMENTS`는 모듈 상수 `demoDataset.ts:13~17`) |
| 네트워크 단절 | 목록 렌더링과 `<Link>` 이동(`DemoModePage.tsx:29`)은 클라이언트 라우팅이라 네트워크 요청이 없다(`DemoModePage.tsx:9~41`). SPA 번들 로딩 동작(오프라인 시)은 확인 불가 | 없음 | 없음 | 없음(근거: 클라이언트 라우팅 `DemoModePage.tsx:29`). 오프라인 시 SPA 번들 로딩: 미확인 |
| 부분 실패(일부 데이터만 실패) | `DEMO_INSTRUMENTS`는 길이 3의 상수 배열(`demoDataset.ts:13~17`)이라 일부만 실패하는 경로가 없다(`DemoModePage.tsx:19~36`). 종목 데이터 자체가 손상된 경우의 동작은 확인 불가 | 없음 | 없음 | 없음(근거: 고정 길이 3 배열 `demoDataset.ts:13~17`, 렌더 루프 `DemoModePage.tsx:19~36`). 종목 데이터 손상 시 동작: 미확인 |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-DemoModePage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/onboarding/DemoModePage.tsx:30`) — "데모 시작" 클릭 | 1. `ProtectedRoute` 통과(로그인, `router.tsx:70~72`). 2. `onboarding_demo_mode` FeatureFlag 활성화(`router.tsx:101`). 3. 사용자가 3종목 중 하나 선택 | `react-router-dom`의 `<Link to>`(`DemoModePage.tsx:29`)가 브라우저 네비게이션 발생. `/onboarding/demo/{instrument.id}` 라우트 매칭 → `DemoChartPage` 렌더(`router.tsx:107~113`). `instrument.id`는 URL 파라미터(`:instrumentId`)로 `DemoChartPage`에 전달 | 1. 비로그인 → `ProtectedRoute`가 로그인 페이지로 리디렉션(이 화면 진입 불가). 2. FeatureFlag 비활성 → `FeatureFlagGate`가 `<DemoModePage />`를 렌더하지 않음(`router.tsx:101`). 두 경우 모두 버튼 클릭 전 차단 | `/onboarding/demo/:instrumentId` → `DemoChartPage` (`router.tsx:107~113`, `frontend/apps/web/src/routes/onboarding/DemoChartPage.tsx`) |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-DemoModePage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-DemoModePage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| TODO | | |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-DemoModePage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-DemoModePage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-DemoModePage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
