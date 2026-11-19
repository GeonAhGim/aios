# J3-MandatesPage — `/mandates`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/MandatesPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/mandates` / `frontend/apps/web/src/routes/mandates/MandatesPage.tsx`
- 여정 소속: J3


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J3-MandatesPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더/투자자) — [확인] `PERSONAS_AND_JOURNEYS.md` §2: P1은 "전략을 만들거나 고르고, 검증하고, 모의->실운용하며 감독". 위임장은 실운용 전 자동 주문 규칙(노출 한도·단종 한도·현금 버퍼·일일 손실 한도·허용 자율성·금지 자산)을 정의하므로 P1의 핵심 과업에 포함됨. P5(자산운용사)도 다중 고객 위임장을 다루나, 이 화면에는 맥락 전환기(고객/펀드 선택)가 없어 P1 중심. |
| 목적(한 문장) | 활성 위임장 리비전을 확인하고, 위임장 초안 작성·개정안 제안·활성화·일시정지·재개를 한 화면에서 관리하여 자동 주문 규칙을 설정·운영한다. |
| 진입 조건 | `frontend/apps/web/src/router.tsx:136` — `/mandates` 라우트. `protect(<MandatesPage />)` 호출로 `ProtectedRoute` 래퍼가 인증을 검사(미인증 시 로그인 페이지로 리다이렉트). |
| 사용자 준비물 | 로그인 세션, 자동 거래 허용 상태(위임장 미설정 시 모든 주문이 차단됨 — `MandatesPage.tsx:82`). 주문 차단 상태를 피하려면 활성 위임장 설정 필요. |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J3-MandatesPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useMandateStatus` | `frontend/packages/shared-hooks/src/useMandates.ts:10` | 1회 조회 — `useQuery`에 폴링 옵션(refetchInterval)과 staleTime 옵션이 설정되지 않음(`useMandates.ts:11-14`). 서버 상태 변경은 mutation의 `onSuccess` → `qc.invalidateQueries({ queryKey: MANDATE_STATUS_QUERY_KEY })`로 캐시 무효화 후 수동 재조회(`MandatesPage.tsx:90`). | `isError`/`error`가 truthy하면(`MandatesPage.tsx:122`), `<MandateActionError error={error} onRetry={() => refetch()} />`로 에러 배너 표시 + 수동 refetch 버튼(`MandatesPage.tsx:127`). |
| `useCreateMandateDraft` | `frontend/packages/shared-hooks/src/useMandates.ts:17` | 변경 요청(POST) — mutation. 성공 시 `qc.invalidateQueries({ queryKey: MANDATE_STATUS_QUERY_KEY })`로 status 캐시 무효화(`useMandates.ts:21`). | `onError: setCreateError`(`MandatesPage.tsx:104`). `createError` 상태가 채워져 `<MandateActionError error={createError} />`로 노출(`MandatesPage.tsx:188-190`). |
| `useProposeMandateAmendment` | `frontend/packages/shared-hooks/src/useMandates.ts:25` | 변경 요청(POST) — mutation. 성공 시 `qc.invalidateQueries({ queryKey: MANDATE_STATUS_QUERY_KEY })`로 status 캐시 무효화(`useMandates.ts:29`). | `onError: setCreateError`(`MandatesPage.tsx:104`). 동일하게 `createError` → `<MandateActionError>`(`MandatesPage.tsx:188-190`). |
| `useActivateMandateRevision` | `frontend/packages/shared-hooks/src/useMandates.ts:33` | 변경 요청(POST) — mutation. 성공 시 `qc.invalidateQueries({ queryKey: MANDATE_STATUS_QUERY_KEY })`로 status 캐시 무효화(`useMandates.ts:38`). | `onError`에서 `setActivateError`(`MandatesPage.tsx:113`). 추가로 `classifyStateConflict(err) === "refetch_retry"`이면 즉시 `refetch()`로 최신 `activeRevision` 반영(`MandatesPage.tsx:116`). `activateError` → `<MandateActionError error={activateError} onRetry={() => refetch()} />`(`MandatesPage.tsx:175-177`). |
| `usePauseMandate` | `frontend/packages/shared-hooks/src/useMandates.ts:42` | 변경 요청(POST) — mutation. 성공 시 `qc.invalidateQueries({ queryKey: MANDATE_STATUS_QUERY_KEY })`로 status 캐시 무효화(`useMandates.ts:46`). | `onError: setLifecycleError`(`MandatesPage.tsx:153`). `lifecycleError` → `<MandateActionError error={lifecycleError} />`(`MandatesPage.tsx:75-77`). |
| `useResumeMandate` | `frontend/packages/shared-hooks/src/useMandates.ts:50` | 변경 요청(POST) — mutation. 성공 시 `qc.invalidateQueries({ queryKey: MANDATE_STATUS_QUERY_KEY })`로 status 캐시 무효화(`useMandates.ts:54`). | `onError: setLifecycleError`(`MandatesPage.tsx:154`). 동일하게 `lifecycleError` → `<MandateActionError>`(`MandatesPage.tsx:75-77`). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J3-MandatesPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `isLoading || !data` (`:133`) | `legacy.mandatesPage.title13` 제목("위임장(Mandate)")(`:137`)과 `LoadingState` 컴포넌트(`:138`) | 없음(데이터 조회 중) | 구현됨(`:133-142`) |
| 빈 상태 | `data.activeRevision`이 null이고 `pendingRevision`도 null인 경우 (`:90`, `:144`, `:162`) | `legacy.mandatesPage.t8` 배지("위임장 미설정(주문 차단)", `Badge tone="warning"`, `:58`), `legacy.mandatesPage.t11` 문단("활성 위임장이 없습니다 — 규칙이 없다는 뜻이 아니라 모든 주문이 차단된다는 뜻입니다.", `:82`), `MandateRuleForm` 제출 라벨 "초안 작성"(`:183`, i18n 키 없음(하드코딩 문자열)) | 초안 작성 제출 (`MandateRuleForm` 제출 → `handleCreate`(`:101-105`) → `createDraft.mutate`(`:103-104`)) | 구현됨(`:58`, `:82`, `:162`, `:182-186`) |
| 정상 | `activeRevision`이 있는 경우 (`:61`). 대기 개정안 존재 여부: `pendingRevision`이 null이 아닐 때(`:162`)와 null일 때(`:180`)로 나뉨 | `legacy.mandatesPage.t7`("현재 활성 리비전", `:54`), `StatusBadge`(리비전 상태 표시, `:56`), `RevisionRules`의 `t1`~`t6`(`:23-28`, 금지 자산이 없으면 "없음" 하드코딩, `:28`). 대기 개정안 있음: `legacy.mandatesPage.t15`("대기 중 개정안", `:161`), `StatusBadge`(개정안 상태, `:165`), `RevisionRules`(개정안 규칙, `:167`), `legacy.mandatesPage.t16` 버튼("활성화", `:169-170`). 대기 개정안 없음: `MandateRuleForm` 제출 라벨 "개정안 제안"(`:183`) | `state === "ACTIVE"`일 때 `legacy.mandatesPage.t9`("일시정지") 버튼(`:65-68`) → `pause.mutate`(`:153`). `state === "PAUSED"`일 때 `legacy.mandatesPage.t10`("재개") 버튼(`:69-72`) → `resume.mutate`(`:154`). 대기 개정안 있을 때 `legacy.mandatesPage.t16`("활성화") 버튼(`:169-170`) → `handleActivate`(`:107-120`) → `activate.mutate`(`:109-110`) | 구현됨(`:54`, `:56`, `:61`, `:65-68`, `:69-72`, `:161`, `:165`, `:167`, `:169-170`, `:183`) |
| 낡은 데이터(신선도 초과) | `useMandateStatus`(`frontend/packages/shared-hooks/src/useMandates.ts:10-15`)에 staleTime과 refetchInterval 설정 없음. `frontend/apps/web/src/routes/mandates/` 안에서 staleTime·refetchInterval·isStale·updatedAt·lastUpdated·fresh·stale 검색 결과 0건. 따라서 신선도 초과 판정 없음. (전역 QueryClient 기본 설정은 미확인 — 범위 밖) | 없음(신선도 표시 없음) | 수동 재시도 버튼은 MandateActionError 컴포넌트의 onRetry에만 있음(`frontend/apps/web/src/routes/mandates/MandatesPage.tsx:127`, `:176`) — refetch() 호출 | 없음(`useMandates.ts:10-15`에 staleTime/refetchInterval 없음, `frontend/apps/web/src/routes/mandates/`에 신선도 관련 코드 0건). 전역 QueryClient 기본 설정은 미확인 |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J3-MandatesPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | mutation(`:104`)에서 서버가 400 반환. `MandateActionError`(`:188-190`)가 `classifyBadRequest`(`MandateActionError.tsx:14`) true 시 `<BadRequestNotice>` 렌더 | `BadRequestNotice` 컴포넌트(서버 메시지 노출, `MandateActionError.tsx:3`, `:14`) | 제출 폼 수정 후 수동 재제출. mutation `onError`(`:104`)는 `onRetry` 전달 안 함 | 구현됨(`:104`, `:188-190`, `MandateActionError.tsx:14`) |
| 인증 필요/세션 만료(401) | `:90`에서 401 반환 시 에러 경로(`:122`) 진입. 라우트 `protect(<MandatesPage />)`(`frontend/apps/web/src/router.tsx:136`)가 미인증 시 로그인 리다이렉트 | 에러 경로(`:122-131`)에서 제목 행(`:126`)과 `<MandateActionError error={error} onRetry={() => refetch()} />`(`:127`) — 제목 문구 키는 legacy.mandatesPage.title12 | `onRetry={() => refetch()}`(`:127`) — 재조회. 401이면 재조회도 실패하므로 수동 로그인 재시도 필요 | 구현됨(`:90`, `:122-131`, `:127`). 구체적인 401 처리 호출명: 없음(전체 isError 경로로 통합) |
| 권한 없음/MFA 필요(403) | mutation(`:153-154`)에서 서버가 403 반환. `MandateActionError`(`:75-77`, `:174-177`)가 `classifyForbidden`(`MandateActionError.tsx:15`) true 시 `<ForbiddenNotice>` 렌더 | `<ForbiddenNotice error={error} />`(`MandateActionError.tsx:5`, `:15`) | `ForbiddenNotice`에 `onRetry` 없음(권한 문제이므로 재시도 무의미) | 구현됨(`:75-77`, `:174-177`, `MandateActionError.tsx:15`). 구체적인 403 처리 호출명: `classifyForbidden` (`MandateActionError.tsx:15`) |
| 충돌/상태 불일치(409) | `useActivateMandateRevision`(`:93`)에서 409 반환. `classifyStateConflict(err) === "refetch_retry"`(`:116`)이면 즉시 `refetch()`(`:90`) | `activateError`(`:98`)가 채워져 `<MandateActionError error={activateError} onRetry={() => refetch()} />`(`:176`) | `onRetry={() => refetch()}`(`:176`) — 최신 `activeRevision`(`:36`) 재조회. `classifyStateConflict`(`:116`)가 "refetch_retry" 반환 시 자동 재조회 | 구현됨(`:107-120`, `:116`, `:174-178`). 구체적인 409 처리 호출명: `classifyStateConflict` (`:116`) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J3-MandatesPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | mutation(`:104`, `:153-154`)에서 서버가 423(`AUTH_ACCOUNT_LOCKED`) 또는 429(`RATE_LIMIT_EXCEEDED`) 반환. `routeApiError`(`errorRouting.ts:125-126`)가 423 → `{ kind: "account_locked", retryAfterSec }`, 429 → `classifyRetry`(`retryable.ts:25`)가 `RATE_LIMIT_EXCEEDED`를 backoff로 분류 → `{ kind: "backoff_retry", afterSec }` 반환. `MandateActionError`(`MandateActionError.tsx:16`)가 `ErrorMessage`로 전달(`:18-24). | `ErrorMessage`(`ErrorMessage.tsx:57-70`)가 danger 배너 + backoff 카운트다운(`:50-53`) + 재시도 버튼(`:66`). 423이면 `retryAfterSec`은 `deriveLockout`(`accountLockout.ts:41`)이 `DEFAULT_ACCOUNT_LOCKOUT_SEC`(60초)로 클램프. 429이면 서버 `retryAfterSec` 사용. | `ErrorMessage`의 재시도 버튼 클릭 시 `onRetry` 호출(`MandateActionError.tsx:23`). 카운트다운 종료 후 활성화. | 구현됨(`MandateActionError.tsx:16-24`, `ErrorMessage.tsx:38-67`, `accountLockout.ts:41`, `errorRouting.ts:125-126`, `retryable.ts:25`) |
| 서버 오류(5xx) | mutation(`:104`, `:153-154`)에서 서버가 500(`INTERNAL_ERROR`)/502(`EXCHANGE_FATAL`)/503(`EXCHANGE_UNAVAILABLE`/`DEPENDENCY_NOT_READY`) 반환. `classifyServerError`(`serverError.ts:39`)가 502/500 → `fatal`, 503 → `retryable`. `routeApiError`(`errorRouting.ts:115-117`)가 `server_fatal` 또는 `backoff_retry` 반환. `MandateActionError`(`MandateActionError.tsx:16`)가 `ErrorMessage`(`:18-24)로 전달. | `ErrorMessage`(`ErrorMessage.tsx:57-70`)가 danger 배너 + `traceId` 노출(`:60`). `server_fatal`이면 재시도 버튼 없음(`canRetry=false`, `:39`). `retryable`(503)이면 backoff 카운트다운 + 재시도 버튼(`:50-67). | `server_fatal`: 재시도 불가(서버 측 문제). `retryable`: `ErrorMessage` 재시도 버튼으로 수동 재시도(`onRetry`, `MandateActionError.tsx:23`). | 구현됨(`serverError.ts:39-52`, `errorRouting.ts:115-117`, `MandateActionError.tsx:16-24`, `ErrorMessage.tsx:38-67`) |
| 네트워크 단절 | `useMandateStatus`(`:90`, `useMandates.ts:10`)의 `useQuery`가 네트워크 실패 시 `isError=true`, `error`는 NetworkError(TypeError). `MandatesPage.tsx:122-131`에서 `isError` 경로 진입 → 전체 화면 대신 `<MandateActionError error={error} onRetry={() => refetch()} />`(`:127`) 표시. `MandateActionError`에서 `classifyBadRequest`/`classifyForbidden` 모두 false, `routeApiError`가 `unknown` 반환(네트워크 오류는 errorCode 없음). `ErrorMessage`가 기본 메시지 노출. | `ErrorMessage`(`ErrorMessage.tsx:57-70`)가 danger 배너 + `getApiErrorMessage` 기본 메시지(`:36`). `classifyRetry`(`:38`)가 네트워크 오류에서 "none" 반환 → `canRetry=false`(`:39`) → 재시도 버튼 없음. `onRetry`가 전달되더라도(`MandateActionError.tsx:23`) 버튼 렌더 안 됨(`:61). | `ErrorMessage` 재시도 버튼 없음(네트워크 오류는 자동 재시도 불가). 사용자가 페이지 새로고침 또는 수동 `refetch`(`:127`의 `onRetry`는 버튼에 바인딩 안 됨). | 구현됨(`MandatesPage.tsx:122-131`, `MandateActionError.tsx:16-24`, `ErrorMessage.tsx:38-39,61`. 네트워크 오류가 `canRetry=false`인 이유: `classifyRetry`(`retryable.ts`)가 errorCode 기반 판정이라 네트워크 오류 미처리 → "none") |
| 부분 실패(일부 데이터만 실패) | `useMandateStatus`(`:90`)는 단일 훅으로 `activeRevision`+`pendingRevision`을 같이 반환 — 이 훅이 실패하면 전체 `isError` 경로(`:122`)로 두 데이터 모두 실패. 그러나 mutation(`createDraft`: `:104`, `proposeAmendment`: `:104`, `activate`: `:113`, `pause`: `:153`, `resume`: `:154`)은 각각 독립적인 `onError` 핸들러와 상태(`createError`: `:97`, `activateError`: `:98`, `lifecycleError`: `:99`)를 가진다. 한 mutation 실패 시 해당 에러 배너만 표시(`:188-190`, `:174-177`, `:75-77`) — 다른 mutation은 계속 작동 가능. | mutation 실패 시 해당 영역에 `<MandateActionError>`(`:188-190`, `:174-177`, `:75-77`) 표시. `useMandateStatus`가 성공하면 데이터 영역(`:144-197`) 전체 렌더 — 일부 mutation 실패해도 데이터 표시는 정상. | 각 에러 배너에 `onRetry` 제공: `createError`(`:188-190`) — 없음(사용자 재제출 필요), `activateError`(`:176`) — `onRetry={() => refetch()}`, `lifecycleError`(`:75-77`) — 없음. | 구현됨(`:97-99`, `:104`, `:113`, `:153-154`, `:75-77`, `:174-178`, `:188-192`). mutation 간 독립 에러 상태가 부분 실패를 허용함. |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J3-MandatesPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/mandates/MandatesPage.tsx:66`) [`일시정지`] | `activeRevision`이 non-null이고 `activeRevision.state === "ACTIVE"` (`:65`). `ActiveRevisionCard` 컴포넌트 내부(`:35-86`). | `pause.mutate()` 호출(`:153`). mutation `onSuccess`에서 `MANDATE_STATUS_QUERY_KEY` 무효화(`useMandates.ts:46`). 상태 재조회(`MandatesPage.tsx:90`)로 `activeRevision.state`가 `"PAUSED"`로 바뀜. | `pause.mutate`의 `onError: setLifecycleError`(`:153`). `lifecycleError`가 채워져 `<MandateActionError error={lifecycleError} />` 배너 노출(`:75-77`). | 동일 화면 `/mandates` 유지 — 파일 전체(`:1-200`)에 라우팅/네비게이션 호출 없음. 상태 재조회로 UI 재렌더. |
| `<Button>` (`frontend/apps/web/src/routes/mandates/MandatesPage.tsx:70`) [`재개`] | `activeRevision`이 non-null이고 `activeRevision.state === "PAUSED"` (`:69`). `ActiveRevisionCard` 내부(`:35-86`). | `resume.mutate()` 호출(`:154`). mutation `onSuccess`에서 `MANDATE_STATUS_QUERY_KEY` 무효화(`useMandates.ts:54`). 상태 재조회(`:90`)로 `activeRevision.state`가 `"ACTIVE"`로 바뀜. | `resume.mutate`의 `onError: setLifecycleError`(`:154`). 동일하게 `lifecycleError` → `<MandateActionError>` 배너(`:75-77`). | 동일 화면 `/mandates` 유지 — 파일 전체(`:1-200`)에 라우팅/네비게이션 호출 없음. 상태 재조회로 UI 재렌더. |
| `<Button>` (`frontend/apps/web/src/routes/mandates/MandatesPage.tsx:169`) [`활성화`] | `pendingRevision`이 non-null (`:162`). `pendingRevision.state`는 `"PROPOSED"`(`:165`). | `activate.mutate({ revisionId })` 호출(`:109-111`). mutation `onSuccess`에서 `MANDATE_STATUS_QUERY_KEY` 무효화(`useMandates.ts:38`). 상태 재조회(`:90`)로 `pendingRevision`이 사라지고 `activeRevision`이 갱신됨. | `handleActivate` 내 `onError: setActivateError`(`:113`). `classifyStateConflict(err) === "refetch_retry"`이면 즉시 `refetch()`(`:116`). `activateError` → `<MandateActionError error={activateError} onRetry={() => refetch()} />` 배너(`:175-177`). | 동일 화면 `/mandates` 유지 — 파일 전체(`:1-200`)에 라우팅/네비게이션 호출 없음. 상태 재조회로 UI 재렌더. |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J3-MandatesPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J3-MandatesPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyStateConflict` (`frontend/apps/web/src/routes/mandates/MandatesPage.tsx:116`) | `getApiErrorMessage(errorCode, message)` (shared-types) → `legacy.errorMessage.t1`(trace_id: "지원코드: {{traceId}}"/"Support code: {{traceId}}"), `t2`("{{remainingSec}}초 후 재시도 가능"/"Retry available in {{remainingSec}}s"), `t3`("다시 시도"/"Retry") | refetch_retry(STATE_CONCURRENCY_CONFLICT): 즉시 refetch()로 최신 activeRevision 반영 후 UI 자동 갱신. idempotency(INTEGRITY_IDEMPOTENCY_CONFLICT): MandateActionError → ErrorMessage에서 재시도 버튼 노출(classifyRetry 기준). invalid_transition(STATE_INVALID_TRANSITION 또는 기타 409): 상태 전이가 애초에 불가능 — 재시도 무의미. ErrorMessage가 classifyRetry로 retry 가능 여부 판정 후 버튼 활성화 제어 | 노출됨. MandateActionError.tsx:21에서 error.traceId를 ErrorMessage 컴포넌트에 전달. ErrorMessage.tsx:60에서 legacy.errorMessage.t1로 "지원코드: {traceId}" 표시 |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J3-MandatesPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J3-MandatesPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J3-MandatesPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
