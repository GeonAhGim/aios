# J5-ResearchPage — `/research`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ResearchPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/research` / `frontend/apps/web/src/routes/research/ResearchPage.tsx`
- 여정 소속: J5


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J5-ResearchPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더/투자자) — J5 여정의 주 페르소나, 공시·뉴스·거시·대안데이터를 검색해 투자 판단 자료로 활용 [확인: PERSONAS_AND_JOURNEYS.md §3 표에서 J5의 주 페르소나 P1] |
| 목적(한 문장) | P1이 공시·뉴스·거시경제·대안데이터를 검색하고, 결과를 차트와 연결하며 소스 상태를 확인하는 리서치 화면 |
| 진입 조건 | `frontend/apps/web/src/router.tsx:122` — `path: "/research"` 라우트, `protect(<ResearchPage />)`로 래핑되어 ProtectedRoute 컴포넌트(동 파일:4)를 통해 로그인 세션 필요 |
| 사용자 준비물 | 로그인 세션, 리서치 데이터 API 접근 권한 (ResearchDataClient prop 주입으로 테스트 가능, source: ResearchPage.tsx:47) |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J5-ResearchPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `searchQuery` (`useQuery`) | `client.search(submitted)` (`api-client/src/clients/researchData.ts:34`) | 사용자 검색 버튼 클릭 시 1회 조회. `enabled: submitted !== null` (`ResearchPage.tsx:154`)으로 submitted가 설정될 때만 실행. refetchInterval/staleTime 없음(매번 새로 조회). 재시도: `searchQuery.refetch()` (`ResearchPage.tsx:255`) | `ResearchErrorBanner` (`ResearchPage.tsx:254~256`) — `routeApiError`(`ResearchPage.tsx:9`)가 refetch_retry/backoff_retry/notRetry로 분류. `ResearchDataRouteNotImplementedError`이면 재시도 버튼 숨김(`ResearchPage.tsx:103`). traceId 노출(`ResearchPage.tsx:108`) |
| `sourcesQuery` (`useQuery`) | `client.listSources()` (`api-client/src/clients/researchData.ts:41`) | 마운트 시 1회 조회. refetchInterval/staleTime 없음. 재시도: `sourcesQuery.refetch()` (`ResearchPage.tsx:300`, `:330`) | `ResearchErrorBanner` (`ResearchPage.tsx:299~300`, `:329~330`) — 동일 오류 분류. 소스 목록 실패 시 커버리지·신선도 패널도 함께 숨김(`ResearchPage.tsx:332`) |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J5-ResearchPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `submitted !== null && searchQuery.isLoading` (`ResearchPage.tsx:253`) | `<LoadingState />` (`ResearchPage.tsx:253`) | 재검색 버튼으로 `searchQuery.refetch()` (`ResearchPage.tsx:255`) | 구현됨 |
| 빈 상태 | `submitted !== null && !searchQuery.isError && !searchQuery.isLoading && items.length === 0` (`ResearchPage.tsx:257~258`) | `<EmptyState>` — "조건에 맞는 항목이 없습니다." (`ResearchPage.tsx:258`, i18n: `research.results.empty`) | 검색어/Kind/종목 변경 후 재검색 | 구현됨 |
| 정상 | `submitted !== null && !searchQuery.isError && !searchQuery.isLoading && items.length > 0` (`ResearchPage.tsx:260~`) | 결과 목록(제목, URL, Kind 배지, 종목 연결 링크/미매핑 배지) (`ResearchPage.tsx:260~294`) | 결과 클릭 → URL为新창 열림(`ResearchPage.tsx:276`), 종목 링크 → `/chart?instrument_id=...`(`ResearchPage.tsx:122`) | 구현됨 |
| 낡은 데이터(신선도 초과) | 미구현 — sourcesQuery에 refetchInterval/staleTime 없음(`ResearchPage.tsx:157~160`) | — | — | 미구현(의도적: 소스 상태는 매번 새로 보는 것이 맞으므로 폴링 없음) |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J5-ResearchPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `validationIssues.length > 0` (`ResearchPage.tsx:231~241`) | `<Alert tone="danger">` — 검증 이슈 목록(현재: `query_required` → "검색어를 입력하세요.", `ResearchPage.tsx:233~240`, i18n: `research.validation.queryRequired`) | 검색어 입력 후 재검색 | 구현됨(검증: `describeResearchSearchIssues`(`shared-types/src/researchData.ts:72~80`)) |
| 인증 필요/세션 만료(401) | `routeApiError`(`ResearchPage.tsx:9`)가 `ApiError`에서 401을 감지 | `ErrorMessage`(`ResearchPage.tsx:105`) — `errorCode`(`ResearchPage.tsx:106`), `message`(`ResearchPage.tsx:107`), `traceId`(`ResearchPage.tsx:108`) 노출 | 재시도(백오프면 `retryAfterSec`(`ResearchPage.tsx:109`)) | 구현됨(`ErrorMessage` 컴포넌트 위임, `ResearchPage.tsx:105`) |
| 권한 없음/MFA 필요(403) | `routeApiError`가 403을 notRetry로 분류(`ResearchPage.tsx:103`) | `ErrorMessage` — 재시도 버튼 없음(onRetry 미渡) | 로그인 재확인 또는 관리자 문의 | 구현됨(예외 처리 `ErrorMessage` 위임) |
| 충돌/상태 불일치(409) | `routeApiError`가 409를 `backoff_retry`로 분류(`ResearchPage.tsx:103`) | `ErrorMessage` — `retryAfterSec` 표시(`ResearchPage.tsx:109`) | 백오프 후 재시도 | 구현됨(`ErrorMessage` 위임) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J5-ResearchPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | `routeApiError`가 429를 `backoff_retry`로 분류(`ResearchPage.tsx:103`) | `ErrorMessage` — `retryAfterSec` 표시(`ResearchPage.tsx:109`) | 백오프 후 재시도 | 구현됨(`ErrorMessage` 위임) |
| 서버 오류(5xx) | `routeApiError`가 5xx를 notRetry로 분류(`ResearchPage.tsx:103`) | `ErrorMessage` — 재시도 버튼 없음 | 서버 복구 후 새로고침 | 구현됨(`ErrorMessage` 위임) |
| 네트워크 단절 | ApiClientBase(`api-client/src/http.ts`)가 네트워크 오류를 `ApiError`로 래핑 | `ErrorMessage` — traceId 노출(`ResearchPage.tsx:108`) | 재시도(`backoff_retry`면 `retryAfterSec`) | 구현됨(ApiClientBase에서 `ApiError`로 래핑) |
| 부분 실패(일부 데이터만 실패) | 검색 API는 단일 호출(`client.search`) — 부분 실패 없음. 소스 목록도 단일 호출(`client.listSources`) | — | — | 해당 없음(단일 호출 모델) |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J5-ResearchPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`ResearchPage.tsx:243`) — "검색" | `onClick={handleSearch}` (`ResearchPage.tsx:243`) | `buildInput()`(`ResearchPage.tsx:166~173`)으로 `ResearchSearchInput` 생성 → `describeResearchSearchIssues`(`shared-types/src/researchData.ts:72`) 검증 → 이슈 없으면 `setSubmitted(input)`(`ResearchPage.tsx:180`) → `searchQuery`가 `enabled`로 API 호출(`ResearchPage.tsx:154`) | `validationIssues.length > 0`이면 `setValidationIssues(issues)`(`ResearchPage.tsx:178`) → `<Alert tone="danger">`(`ResearchPage.tsx:233`)로 오류 표시, API 호출 안 함 | — |
| `<Link>` (`ResearchPage.tsx:121~126`) — "차트에서 보기" | `item.instrumentId`가 truthy (`ResearchPage.tsx:119`) | `/chart?instrument_id=${encodeURIComponent(item.instrumentId)}`으로 라우터 이동(`ResearchPage.tsx:122`) | — | `/chart` |
| `<Badge>` (`ResearchPage.tsx:134`) — "미매핑" | `item.instrumentId`가 null (`ResearchPage.tsx:129~132`) | `unmappedReason`에 따라 "not_found" 또는 "no_deterministic_key" 메시지 표시(`ResearchPage.tsx:129~132`) | — | — |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J5-ResearchPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | 해당 없음 — 리서치 데이터 검색/조회 화면으로, 금액 거래·주문·이체·정산 등 금전 행동이 없음 |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J5-ResearchPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `routeApiError` (`ResearchPage.tsx:9`) | `routeApiError`는 i18n 키 없음 — `ApiError`의 `errorCode`(`ResearchPage.tsx:106`)를 `ErrorMessage`로 전달 | `routeApiError`가 refetch_retry/backoff_retry/notRetry 분류(`ResearchPage.tsx:103`) — 재시도 가능하면 `onRetry`(`ResearchPage.tsx:110`), 백오프면 `retryAfterSec`(`ResearchPage.tsx:109`) | `ApiError.traceId`를 `ErrorMessage.traceId`로 전달(`ResearchPage.tsx:108`) |
| `Alert` (`ResearchPage.tsx:18`) | 검증 오류: `research.validation.queryRequired` — "검색어를 입력하세요." (`ResearchPage.tsx:92`, `shared-types/src/researchData.ts:77`) | 검색어 입력 후 재검색 | 없음(클라이언트 검증 오류) |
| `ErrorMessage` (`ResearchPage.tsx:105`) | `ErrorMessage` 컴포넌트 내부 i18n 키(컴포넌트 구현에 따름) — `errorCode`(`ResearchPage.tsx:106`)와 `message`(`ResearchPage.tsx:107`) 표시 | 재시도 가능하면 retry 버튼, 불가하면 새로고침 안내 | `ApiError.traceId` 전달(`ResearchPage.tsx:108`) — `ResearchDataRouteNotImplementedError`는 traceId 없음 |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J5-ResearchPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | `<Field>` 컴포넌트가 label 사용(`ResearchPage.tsx:194`, `:219`, `:226`), `<input type="checkbox">`에 `id`(`ResearchPage.tsx:207`), `data-testid`로 테스트용 식별자 제공(`ResearchPage.tsx:196`, `:202`, `:209`, `:221`, `:227`, `:232`, `:243`) | `<input type="checkbox">`에 `aria-label` 또는 `aria-checked` 명시 없음 — `<label>`으로 감싸서 접근성 우회하므로 AA 준수 여부 높음(미확인: Lighthouse 검증 필요) |
| i18n/지역화 | 모든 사용자 텍스트가 `t()`로 감싸짐(`ResearchPage.tsx:62~94`, `:189~332`). Facts 시트(`ResearchPage.facts.md`)에 ko/en 매핑 28개 기록됨. `kindLabel`/`redistributionLabel`은 switch 고정 리터럴(`ResearchPage.tsx:62~87`) | `ResearchDataRouteNotImplementedError` 메시지가 한글 고정("리서치 데이터 API가 아직 제공되지 않습니다.", `api-client/src/clients/researchData.ts:27`) — i18n 키 아님. 영어 환경에서 한글 노출 |
| 성능 예산 | `useMemo`로 클라이언트 메모이제이션(`ResearchPage.tsx:142`). `searchQuery`는 `enabled` 제어(불필요한 호출 없음). `sourcesQuery`는 마운트 시 1회. 폴링 없음 | — |
| 텔레메트리 | `ErrorMessage` 컴포넌트가 `traceId` prop 받음(`ResearchPage.tsx:108`) — 하지만 traceId를 외부로 전송하는 코드는 이 파일에서 보이지 않음 | traceId 수집/전송 구현 여부 미확인(하위 컴포넌트 `ErrorMessage` 구현체 확인 필요) |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J5-ResearchPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | 빈 검색어로 "검색" 클릭 시 `<Alert tone="danger">`(`ResearchPage.tsx:233`)이 표시되고 API 호출이 발생하지 않는다 | `describeResearchSearchIssues`(`shared-types/src/researchData.ts:72`)가 빈 query에 대해 `[{code: "query_required"}]`를 반환(`:77`), `handleSearch`(`ResearchPage.tsx:177~179`)가 이슈가 있으면 `setSubmitted`를 호출하지 않음 |
| A2 | 검색 버튼 클릭 후 `searchQuery`가 API를 호출하고, 로딩 중에는 `<LoadingState />`(`ResearchPage.tsx:253`)이, 완료 후 결과가 없으면 `<EmptyState>`(`ResearchPage.tsx:258`)가 표시된다 | `enabled: submitted !== null`(`ResearchPage.tsx:154`)로 조건부 실행, `isLoading`/`isError`/`items.length === 0` 분기(`ResearchPage.tsx:253~258`) |
| A3 | 마운트 시 소스 목록(`sourcesQuery`)이 `client.listSources()`(`api-client/src/clients/researchData.ts:41`)를 1회 호출하고, 에러 시 `<ResearchErrorBanner>`(`ResearchPage.tsx:299~300`)가 표시된다 | `useQuery`(`ResearchPage.tsx:157~160`)는 기본 enabled=true, `isError` 분기(`ResearchPage.tsx:299~300`) |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J5-ResearchPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | 소스 상태 데이터의 신선도 보장(폴링 또는 staleTime) | sourcesQuery에 refetchInterval/staleTime 없음(`ResearchPage.tsx:157~160`) — 마운트 시 1회만 조회 | 낮(소스 상태는 수동 새로고침으로 충분하다고 판단) | 미처리(의도적) |
| G2 | `ResearchDataRouteNotImplementedError` 메시지 i18n | `api-client/src/clients/researchData.ts:27`에서 한글 고정 메시지("리서치 데이터 API가 아직 제공되지 않습니다.") | 낮(Phase 0: 라우터 이전의 예외 상태이므로 i18n 우선순위 낮음) | 미처리(의도적) |
| G3 | 검색 결과 페이지네이션 | `ResearchSearchResponse.total`/`truncated` 필드 있음(`shared-types/src/researchData.ts:36~40`)但 화면에서 `truncated` 경고만 표시(`ResearchPage.tsx:265`) — 페이지네이션 없음 | 낮(현재는 탐색용 검색이므로 전체 목록 대신 1회 조회로 충분) | 미처리(의도적) |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
