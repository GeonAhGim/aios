# J3-MandatePage — `/compliance/mandate`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/MandatePage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/compliance/mandate` / `frontend/apps/web/src/routes/compliance/MandatePage.tsx`
- 여정 소속: J3


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J3-MandatePage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1(개인 트레이더/투자자) — J3 여정(모의 주문)의 컴플라이언스 설정 단계. P5(자산운용사)도 고객별 위임장 설정에 사용. P11(감사자·컴플라이언스)은 읽기 전용 참조. [PERSONAS_AND_JOURNEYS.md §2] |
| 목적(한 문장) | 테넌트별 위임장(노출·한도·자산 금지 규칙)을 조회·편집·승인하고, 초안/개정안 제출과 리비전 활성화·일시정지·재개로 자동 주문 필터를 관리한다. [MandatePage.tsx:46~201] |
| 진입 조건 | `router.tsx:138` 경로 `/compliance/mandate` 접근. `protect()` 호출이 `ProtectedRoute`로 인증된 세션만 통과시킨다. feature flag 없음. [router.tsx:70~72, 138] |
| 사용자 준비물 | 로그인 세션, 테넌트 컨텍스트(위임장은 테넌트 단위, GET /mandates/status가 테넌트당 active/pending 리비전 각 최대 1건만 반환한다). P5는 고객/펀드/포트폴리오 맥락 선택 완료. [MandatePage.tsx:18~22] |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J3-MandatePage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useMandateStatus` | `frontend/packages/shared-hooks/src/useMandates.ts:10` | 1회 조회(`useQuery`). 폴링/자동 갱신 옵션 설정 없음. `refetch()` 수동 재조회로만 갱신. [useMandates.ts:11~14, MandatePage.tsx:48] | `isError` → `MandateActionError` 컴포넌트에 `error` 객체 전달, `onRetry={() => refetch()}` 버튼 노출. 500 등 서버 오류 시 동일 경로. [MandatePage.tsx:83~92] |
| `useCreateMandateDraft` | `frontend/packages/shared-hooks/src/useMandates.ts:17` | mutation(1회 호출). 성공 시 `qc.invalidateQueries({ queryKey: MANDATE_STATUS_QUERY_KEY })`로 상태 캐시 무효화. [useMandates.ts:19~22] | `onError: setCreateError` → `createError` 상태에 저장, `MandateActionError`에 전달. [MandatePage.tsx:55, 62, 190~193] |
| `useProposeMandateAmendment` | `frontend/packages/shared-hooks/src/useMandates.ts:25` | mutation(1회 호출). 성공 시 `qc.invalidateQueries({ queryKey: MANDATE_STATUS_QUERY_KEY })`. [useMandates.ts:27~30] | `onError: setCreateError` (handleCreate 내부). `createError` → `MandateActionError`. [MandatePage.tsx:62, 190~193] |
| `useActivateMandateRevision` | `frontend/packages/shared-hooks/src/useMandates.ts:33` | mutation(1회 호출). 성공 시 `qc.invalidateQueries({ queryKey: MANDATE_STATUS_QUERY_KEY })`. [useMandates.ts:35~38] | `onError`에서 `classifyStateConflict(err) === "refetch_retry"`면 409 경합으로 판단, `refetch()`로 최신 상태 재조회 후 재시도 가능. 그 외는 `setActivateError` → `MandateActionError`. [MandatePage.tsx:70~75] |
| `usePauseMandate` | `frontend/packages/shared-hooks/src/useMandates.ts:42` | mutation(1회 호출). 성공 시 `qc.invalidateQueries({ queryKey: MANDATE_STATUS_QUERY_KEY })`. [useMandates.ts:44~47] | `setLifecycleError` → `lifecycleError` → `MandateActionError`. [MandatePage.tsx:57, 158] |
| `useResumeMandate` | `frontend/packages/shared-hooks/src/useMandates.ts:50` | mutation(1회 호출). 성공 시 `qc.invalidateQueries({ queryKey: MANDATE_STATUS_QUERY_KEY })`. [useMandates.ts:52~55] | `setLifecycleError` → `lifecycleError` → `MandateActionError`. [MandatePage.tsx:57, 158] |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J3-MandatePage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `isLoading === true` 또는 `data === null` (`useMandateStatus` 초기 조회 중) | `mandatePage.pageTitle`("위임장 편집·승인") 페이지 제목 + `<LoadingState />` 로딩 인디케이터 | 없음(조회 완료까지 대기) | 구현됨 (`MandatePage.tsx:94~103`: `isLoading \|\| !data` 조건 분기, `LoadingState` 렌더) |
| 빈 상태 | `data` 조회 완료, `activeRevision`도 `pendingRevision`도 없음(`listItems.length === 0`) | `mandatePage.listTitle`("위임장 목록") 제목 + `mandatePage.listEmpty`("등록된 위임장이 없습니다.") 안내 문구 + `legacy.mandatesPage.t8`("위임장 미설정(주문 차단)") 경고 배지(활성 리비전 없을 시) | 초안 작성 또는 개정안 제안: `<MandateRuleForm>` 제출(`mandatePage.draftSubmitLabel` 또는 `mandatePage.amendSubmitLabel`) | 구현됨 (`MandatePage.tsx:120~121`: 빈 목록 렌더, `MandatePage.tsx:118`: t8 배지, `MandatePage.tsx:184~188`: MandateRuleForm) |
| 정상 | `data` 조회 완료, `activeRevision` 또는 `pendingRevision` 중 최소 1건 존재 | `mandatePage.listTitle` + 목록 항목(각 항목: `mandatePage.activeLabel`/`mandatePage.pendingLabel` 라벨 + `StatusBadge` + 한도 정보 `t1~t5` + 금지 자산 `t6`) + 활성 리비전 카드(`legacy.mandatesPage.t7` + pause/resume 버튼) + 대기 리비전 카드(`legacy.mandatesPage.t15` + activate 버튼 또는 초안/개정안 폼) | pause(`legacy.mandatesPage.t9`), resume(`legacy.mandatesPage.t10`), activate(`legacy.mandatesPage.t16`), 개정안 제안/초안 작성 | 구현됨 (`MandatePage.tsx:107~108`: 목록 빌드, `MandatePage.tsx:124~126`: 목록 렌더, `MandatePage.tsx:131~162`: 활성 리비전 카드, `MandatePage.tsx:164~197`: 대기 리비전 카드) |
| 낡은 데이터(신선도 초과) | `useMandateStatus`는 캐시 신선도 옵션 없이 1회 조회만 수행 — 명시적 신선도 검사 없음 | 해당 없음 | 해당 없음 | 없음 (`useMandates.ts:11~14`: 캐시 신선도 옵션 설정 없음, `MandatePage.tsx:48`: 기본 `useQuery` 사용) |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J3-MandatePage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | 생성·개정 또는 activate mutation이 400 반환 | 정상 렌더 분기에서 `mandatePage.pageTitle`("위임장 편집·승인") 제목(`MandatePage.tsx:113`) 노출. mutation 400 시 `MandateActionError` 컴포넌트 노출. `MandateActionError` 내부에서 `classifyBadRequest(error)`(`MandateActionError.tsx:14`)가 true면 `BadRequestNotice`, 그 외 `ErrorMessage`(MandateActionError.tsx:16~17) | 없음(페이지 전용 재시도 버튼 없음). 자식 컴포넌트(`MandateActionError`) 경유: `classifyBadRequest`→`BadRequestNotice`(MandateActionError.tsx:14). `BadRequestNotice` 내 문구 키 확인 안 함 → `미확인`. 폼 자체 입력 검증(`MandateRuleForm`)은 이 파일 밖 → `미확인`. activate 400: `setActivateError`→동일 경로(`MandatePage.tsx:71, :176~:180`). pause/resume 400: `setLifecycleError`→동일 경로(:140, :151, :156~:160). `classifyStateConflict`는 400에 `null`(stateConflict.ts:34) | 부분 구현(페이지 직접 분기 없음. 공통 경로(`MandateActionError`)가 400를 `BadRequestNotice`(MandateActionError.tsx:14) 또는 `ErrorMessage`(MandateActionError.tsx:16)로 처리) |
| 인증 필요/세션 만료(401) | 세션 만료 또는 미인증 요청 | `useMandateStatus` 조회 실패 시 `isError` 분기(`MandatePage.tsx:83~91`): `mandatePage.pageTitle`("위임장 편집·승인") 제목(`:113`의 정상 분기와 동일 경로) + `MandateActionError`. mutation 401 시 `MandateActionError` 노출. `classifyBadRequest`(MandateActionError.tsx:14)도 `classifyForbidden`(MandateActionError.tsx:15)도 401을 잡지 못해 `routeApiError`(MandateActionError.tsx:16)로 폴백 → `ErrorMessage`(MandateActionError.tsx:18). 문구 키는 `routeApiError`/`ErrorMessage` 내부에 있으므로 `미확인`. `classifyStateConflict`는 401에 `null`(stateConflict.ts:34) | 없음(페이지 전용 분기 없음) | 없음(페이지 직접 분기 없음. 공통 경로(`MandateActionError`)가 401을 `ErrorMessage`(MandateActionError.tsx:16~18)로 처리하나, 문구 키와 렌더 결과는 `routeApiError`/`ErrorMessage` 내부 미확인 → `미확인`) |
| 권한 없음/MFA 필요(403) | 권한 부족 또는 MFA 요구 | `useMandateStatus` 조회 실패 시 `isError` 분기(`MandatePage.tsx:83~91`): `mandatePage.pageTitle`("위임장 편집·승인") 제목(`:113`의 정상 분기와 동일 경로) + `MandateActionError`. mutation 403 시 `MandateActionError` 노출. `classifyForbidden(error)`(`MandateActionError.tsx:15`)가 true면 `ForbiddenNotice`, 그 외 `ErrorMessage`(MandateActionError.tsx:16~17). `ForbiddenNotice` 내 문구 키 확인 안 함 → `미확인`. MFA 안내 확인 안 함 → `미확인`. `classifyStateConflict`는 403에 `null`(stateConflict.ts:34) | 없음(페이지 전용 분기 없음) | 없음(페이지 직접 분기 없음. 공통 경로(`MandateActionError`)가 403을 `ForbiddenNotice`(MandateActionError.tsx:15) 또는 `ErrorMessage`(MandateActionError.tsx:16~17)로 처리하나, 문구 키와 렌더 결과는 `ForbiddenNotice` 내부 미확인 → `미확인`) |
| 충돌/상태 불일치(409) | activate·초안/개정·pause/resume mutation이 409 반환 | 정상 렌더 분기에서 `mandatePage.pageTitle`("위임장 편집·승인") 제목(`MandatePage.tsx:113`) 노출. activate 경합(`STATE_CONCURRENCY_CONFLICT`): `classifyStateConflict(err)==="refetch_retry"`(`MandatePage.tsx:74`) → `refetch()`로 최신 상태 재조회(:70~:75). 그 외 409: `setActivateError`→`MandateActionError`(:71, :176~:180). 초안/개정 409: `setCreateError`→`MandateActionError`(:62, :190~:193). pause/resume 409: `setLifecycleError`→`MandateActionError`(:140, :151, :156~:160) | activate 경합만 `refetch()` 재시도(`MandatePage.tsx:74`). 그 외 409는 재시도 버튼 없음(`MandateActionError`의 `ErrorMessage`(MandateActionError.tsx:18)가 `onRetry` 받지만 activate 409 외에는 전달 안 함). 문구 키는 별도 없으니 `미확인`. `classifyStateConflict` 매핑: `STATE_CONCURRENCY_CONFLICT`→`"refetch_retry"`, 그 외 409→`"invalid_transition"`(stateConflict.ts:37~39, stateConflict.test.ts:5~28) | 구현됨(activate 경로만 `refetch_retry` 재조회). 초안/개정·pause/resume 409는 오류 노출만. "재시도 버튼 없음" 명시 |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J3-MandatePage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 서버가 423 Locked 또는 429 Too Many Response 반환. `classifyRetry(err)`가 `backoff` 갈래로 분류(`shared-types/retryable.ts:45~50`: `BACKOFF_ERROR_CODES` 확인). `routeApiError`가 `{ kind: "backoff_retry", afterSec }` 반환(`shared-types/errorRouting.ts:119~121`). | `MandateActionError` → `ErrorMessage` 컴포넌트: `getApiErrorMessage(errorCode)`로 매핑된 오류 문구(`Alert tone="danger"`) + `retryAfterSec` 초 카운트다운 + 재시도 버튼(`legacy.errorMessage.t3`). 버튼은 `remainingSec > 0` 시 비활성화. | 재시도 버튼 클릭 시 `onRetry` 콜백 호출. 활성화는 `remainingSec`이 0이 된 후. | 구현됨 (`MandateActionError.tsx:16~24`: `routeApiError` 경로 → `ErrorMessage`. `ErrorMessage.tsx:38~39`: `classifyRetry`로 `backoff` 판정. `ErrorMessage.tsx:41~53`: `remainingSec` 카운트다운. `ErrorMessage.tsx:66`: `disabled={remainingSec > 0}`) |
| 서버 오류(5xx) | 서버가 500/502/503 등 반환. `classifyServerError(err)`가 `retryable`(RETRYABLE_SERVER_ERROR_CODES) 또는 `fatal`(FATAL_SERVER_ERROR_CODES, `shared-types/serverError.ts:42~49`). `routeApiError`가 `{ kind: "backoff_retry" }` 또는 `{ kind: "server_fatal", traceId }` 반환(`shared-types/errorRouting.ts:115~117`). | `MandateActionError` → `ErrorMessage`: 오류 문구 + `traceId` 노출(`legacy.errorMessage.t1`). `retryable`이면 재시도 버튼(즉시 활성화). `fatal`이면 `traceId`만 노출, 재시도 버튼 없음(`canRetry`가 false). | `retryable`이면 재시도 버튼으로 수동 재시도. `fatal`이면 재시도 버튼 없음(서버 측 복구 필요). | 구현됨 (`MandateActionError.tsx:16~24`: `routeApiError` → `ErrorMessage`. `ErrorMessage.tsx:19`: `traceId` 전달. `ErrorMessage.tsx:38~39`: `classifyRetry` → `canRetry`. `ErrorMessage.tsx:60`: `traceId` 노출. `ErrorMessage.tsx:61~69`: `canRetry && onRetry` 조건부 버튼) |
| 네트워크 단절 | 클라이언트-서버 간 연결 실패(HTTP 요청 실패). React Query가 `isError: true`로 전환. `useMandateStatus`의 `error`에 `Error` 인스턴스 저장. | `MandatePage.tsx:83~92`: `isError` 분기 → `MandateActionError` 렌더 + `onRetry={() => refetch()}`. `MandateActionError.tsx:14~16`: `classifyBadRequest`/`classifyForbidden`이 false → `routeApiError` → `ErrorMessage`. 네트워크 예외는 `ApiError`가 아님 → `errorCode`/`traceId` 없음. 재시도 버튼 노출(`onRetry` 전달됨). | 재시도 버튼 클릭 시 `refetch()`로 `GET /mandates/status` 재조회. | 구현됨 (`MandatePage.tsx:48`: `isError`/`error`/`refetch` 구조 분해. `MandatePage.tsx:83~92`: `isError` 분기, `MandateActionError`에 `onRetry={() => refetch()}` 전달. `MandateActionError.tsx:23`: `onRetry` → `ErrorMessage` 전달) |
| 부분 실패(일부 데이터만 실패) | `useMandateStatus`는 단일 쿼리(GET /mandates/status)로 activeRevision과 pendingRevision을 한 번에 반환 — 부분 데이터 반환 없음. mutation(`createDraft`, `proposeAmendment`, `activate`, `pause`, `resume`)은 각각 독립적 에러 상태(`createError`, `activateError`, `lifecycleError`)를 가지며, 실패 시 해당 mutation만 실패하고 다른 mutation은 정상. | 각 mutation 실패 시 별도 에러 영역에 `MandateActionError` 노출. 예: activate 실패 → `activateError` → `MandateActionError` (176~179행). pause/resume 실패 → `lifecycleError` → `MandateActionError` (158행). 초안/개정안 실패 → `createError` → `MandateActionError` (190~194행). | 각 에러 영역에 `onRetry` 제공 여부: activate 에러는 `onRetry={() => refetch()}` 제공(178행). 초안/개정안 에러는 `onRetry` 없음(192행). pause/resume 에러는 `onRetry` 없음(158행). | 구현됨 (`MandatePage.tsx:55~57`: 3개 에러 상태 분리. `MandatePage.tsx:70~75`: activate onError에서 `setActivateError` + `classifyStateConflict` 409 재조회. `MandatePage.tsx:176~179`: activateError 노출 + `onRetry={() => refetch()}`. `MandatePage.tsx:190~194`: createError 노출(onRetry 없음). `MandatePage.tsx:158`: lifecycleError 노출(onRetry 없음)) |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J3-MandatePage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| pause 버튼 (`frontend/apps/web/src/routes/compliance/MandatePage.tsx:135`) | 활성 카드 표시 조건(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:131`: `ACTIVE` 또는 `PAUSED`) 안에서 `activeRevision.state === "ACTIVE"`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:134`)일 때만 렌더. 라벨 `legacy.mandatesPage.t9`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:142`). `variant="secondary"`, `size="sm"`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:137~138`). `loading={pause.isPending}`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:139`) — `Button.tsx:39`에서 `disabled={disabled || loading}`이므로 loading 중 클릭 비활성화 확인됨. | `pause.mutate` 호출(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:140`) → 훅(`frontend/packages/shared-hooks/src/useMandates.ts:44~47`)의 `onSuccess`가 `MANDATE_STATUS_QUERY_KEY` 캐시 무효화 → React Query 자동 재조회로 `activeRevision.state`가 `"PAUSED"`로 바뀜 → 카드가 PAUSED 상태 렌더. 성공 시 토스트/알림 등 별도 피드백 없음(미확인). | `onError: setLifecycleError`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:140`) → `lifecycleError` 상태에 저장 → 활성 카드 하단 `MandateActionError`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:156~160`)에 `error={lifecycleError}` 전달, `onRetry` 인자 없음(재시도 버튼 없음). 409도 동일 경로. | 이동 없음(같은 `/compliance/mandate`, 파일 전체에 navigate/Link 없음). 상태만 바뀜. |
| resume 버튼 (`frontend/apps/web/src/routes/compliance/MandatePage.tsx:146`) | 활성 카드 표시 조건(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:131`) 안에서 `activeRevision.state === "PAUSED"`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:145`)일 때만 렌더. 라벨 `legacy.mandatesPage.t10`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:153`). `variant="secondary"`, `size="sm"`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:148~149`). `loading={resume.isPending}`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:150`) — `Button.tsx:39`에서 클릭 비활성화 확인됨. | `resume.mutate` 호출(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:151`) → 훅(`frontend/packages/shared-hooks/src/useMandates.ts:52~55`)의 `onSuccess`가 `MANDATE_STATUS_QUERY_KEY` 캐시 무효화 → React Query 자동 재조회로 `activeRevision.state`가 `"ACTIVE"`로 바뀜 → 카드가 ACTIVE 상태 렌더. 성공 시 토스트/알림 등 별도 피드백 없음(미확인). | `onError: setLifecycleError`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:151`) → `lifecycleError` 상태에 저장 → 활성 카드 하단 `MandateActionError`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:156~160`)에 전달, `onRetry` 인자 없음. 409도 동일 경로. | 이동 없음(같은 `/compliance/mandate`). 상태만 바뀜. |
| activate 버튼 (`frontend/apps/web/src/routes/compliance/MandatePage.tsx:169`) | `pendingRevision`이 존재할 때만(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:166`의 `pendingRevision ?` 분기). 대기 카드에 PENDING 개정안이 없으면 이 버튼 대신 `MandateRuleForm`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:183~188`)이 나옴. 라벨 `legacy.mandatesPage.t16`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:170`). `type="button"`, `loading={activate.isPending}`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:169`) — `Button.tsx:39`에서 `disabled={disabled || loading}`이므로 loading 중 클릭 비활성화 확인됨. `disabled` 속성 명시 없음. | `handleActivate(pendingRevision.id)`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:65~78`, `frontend/apps/web/src/routes/compliance/MandatePage.tsx:169` 클릭 시 호출) → `activate.mutate`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:67`) → 훅(`frontend/packages/shared-hooks/src/useMandates.ts:36~39`)의 `onSuccess`가 `MANDATE_STATUS_QUERY_KEY` 캐시 무효화 → React Query 자동 재조회로 `pendingRevision`이 사라지고 활성 카드로 전환. 성공 시 토스트/알림 등 별도 피드백 없음(미확인). | `setActivateError(err)`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:71`) → 대기 카드 하단 `MandateActionError`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:176~180`)에 `error={activateError}` 전달, `onRetry={() => refetch()}`(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:178`)로 수동 재조회 가능. 409(`classifyStateConflict` 결과가 `"refetch_retry"`, `frontend/apps/web/src/routes/compliance/MandatePage.tsx:74`)면 추가로 `refetch()`로 최신 상태 재조회. 다른 실패는 재시도 재조회만 가능. | 이동 없음. |
| activate 버튼 비활성화 여부 | 버튼에 `disabled` 속성은 명시되지 않음(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:169`). `loading={activate.isPending}`만 전달되며, `Button.tsx:39`의 `disabled={disabled || loading}`에 의해 loading 중 자동 비활성화됨. | — | — | — |

<!-- BLOCK:J3-MandatePage:act-gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | `lifecycleError`는 pause/resume 클릭 시 초기화하지 않음(`frontend/apps/web/src/routes/compliance/MandatePage.tsx:140`, `frontend/apps/web/src/routes/compliance/MandatePage.tsx:151`은 `onError`만 전달, `setLifecycleError(null)` 호출이 파일 어디에도 없음). 따라서 한 번 난 pause/resume 오류가 이후 성공해도 남음. `activateError`는 `frontend/apps/web/src/routes/compliance/MandatePage.tsx:66`에서 초기화(`setActivateError(null)`)되므로 이 문제가 없음. | — | 정보 | — |
<!-- /BLOCK -->
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J3-MandatePage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J3-MandatePage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyStateConflict` (`frontend/apps/web/src/routes/compliance/MandatePage.tsx:74`) | `getApiErrorMessage(errorCode)` (shared-types/apiError.ts:106) — `EXACT_MESSAGES` 매핑: `STATE_CONCURRENCY_CONFLICT` → "동시 수정 경합: 최신 상태로 새로고침하세요.", `STATE_INVALID_TRANSITION` → "허용되지 않은 상태 전이입니다.", `INTEGRITY_IDEMPOTENCY_CONFLICT` → "중복 제출 감지: 이미 처리된 요청입니다." (apiError.ts:42~58 EXACT_MESSAGES). 미지 코드는 `DEFAULT_API_ERROR_MESSAGE` → "요청을 처리하지 못했습니다. 잠시 후 다시 시도해주세요." (apiError.ts:94). | 1) `STATE_CONCURRENCY_CONFLICT` → `classifyStateConflict`가 `"refetch_retry"` 반환 → `refetch()` 자동 재조회 (`MandatePage.tsx:74`). 사용자가 별도 행동할 필요 없음. 2) `STATE_INVALID_TRANSITION` 또는 미지 409 → `"invalid_transition"` → `activateError` 설정 → `MandateActionError` → `ErrorMessage` 배너 노출. 재시도 버튼은 `classifyRetry`가 `"none"`이 아닐 때만 표시 (ErrorMessage.tsx:39~61). 3) `INTEGRITY_IDEMPOTENCY_CONFLICT` → `"idempotency"` → 동일하게 `ErrorMessage` 노출. | 노출함. `MandateActionError` → `ErrorMessage` (ErrorMessage.tsx:19, 60)가 `error instanceof ApiError ? error.traceId : undefined`로 traceId를 전달하고, `legacy.errorMessage.t1`("지원코드: {{traceId}}")로 표시한다. |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J3-MandatePage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J3-MandatePage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J3-MandatePage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
