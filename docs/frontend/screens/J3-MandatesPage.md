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
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J3-MandatesPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/mandates/MandatesPage.tsx:66`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/mandates/MandatesPage.tsx:70`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/mandates/MandatesPage.tsx:169`) | TODO | TODO | TODO | TODO |
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
| `classifyStateConflict` (`frontend/apps/web/src/routes/mandates/MandatesPage.tsx:116`) | TODO | TODO | TODO |
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
