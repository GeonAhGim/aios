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
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J3-MandatePage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J3-MandatePage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/compliance/MandatePage.tsx:135`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/compliance/MandatePage.tsx:146`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/compliance/MandatePage.tsx:169`) | TODO | TODO | TODO | TODO |
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
| `classifyStateConflict` (`frontend/apps/web/src/routes/compliance/MandatePage.tsx:74`) | TODO | TODO | TODO |
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
