# J6-DecisionHistoryPage — `/decisions/history`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/DecisionHistoryPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/decisions/history` / `frontend/apps/web/src/routes/decisions/DecisionHistoryPage.tsx`
- 여정 소속: J6


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J6-DecisionHistoryPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더/투자자) — J1~J6 전체 여정의 주체. `PERSONAS_AND_JOURNEYS.md:22` [확인] |
| 목적(한 문장) | 판정(왜 막혔나)과 이벤트 계보(무슨 일이 있었나)를 한 화면에서 나란히 조회하여 차단 사유를 역추적·재현한다. |
| 진입 조건 | `/decisions/history` 라우트(`router.tsx:139`) — `protect()`로 보호됨(인증 필요). 별도 플래그/가드 없음. |
| 사용자 준비물 | 없음. 읽기 전용 뷰어이며 외부 입력 요소가 없다. |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J6-DecisionHistoryPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useComplianceDecisionLookup` (ComplianceDecisionLookupPanel) | `@aios/shared-hooks` → `apiClient.getComplianceDecision(decisionId)` → `GET /v1/foundation/compliance/decisions/{decisionId}` (CM-13 explain, `shared-hooks/src/useCompliance.ts:10`) | 1회 조회 — `useMutation`(폴링 없음). 사용자가 decisionId 입력 후 버튼 클릭 시 `mutate()` 호출(`ComplianceDecisionLookupPanel.tsx:44`). 마운트 시 자동 조회 없음(`useCompliance.ts:5-6`). | `ComplianceActionError` 컴포넌트(`ComplianceActionError.tsx:11-24`)가 `routeApiError()`로 분류: 400→`BadRequestNotice`, 403→`ForbiddenNotice`, 그 외→`ErrorMessage`(trace_id 노출, `retryAfterSec` 설정 시 재시도 버튼). 로컬 상태 `error: unknown`(`ComplianceDecisionLookupPanel.tsx:31`)에 저장 후 `error !== null` 조건으로 렌더(`ComplianceDecisionLookupPanel.tsx:61-65`). |
| `usePositionJournal` (EventLineageLookupPanel → PositionJournalPanel) | `frontend/apps/web/src/hooks/usePositions.ts:66` → `client.getPositionJournal({positionKey, cursor, limit})` → `GET /v1/positions/{position_key}/journal` (FA-14/LB-19, `PositionJournalPanel.tsx:14`) | 1회 조회 + 커서 페이지네이션 — `useQuery` + `useCursorPage`(`PositionJournalPanel.tsx:60-61`). 사용자가 positionKey 입력 후 버튼 클릭 시 `setSubmitted()`로 키 전달(`EventLineageLookupPanel.tsx:40`). `enabled: open` — 패널 확장 시에만 조회(`PositionJournalPanel.tsx:61`). `key={submitted}`로 PositionJournalPanel 강제 재마운트(`EventLineageLookupPanel.tsx:59`). 폴링 없음. | `PositionsQueryError` 컴포넌트(`PositionsQueryError.tsx:19-37`)가 `routeApiError()`로 분류: 404→`NotFoundState`("저널이 없습니다.", 재시도 버튼 없음), 403→`ForbiddenNotice`, 그 외→`ErrorMessage`(trace_id 노출, `refetch_retry`/`backoff_retry`일 때만 `onRetry` 배선). `query.isError` 조건으로 렌더(`PositionJournalPanel.tsx:88`). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J6-DecisionHistoryPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | 페이지 자체는 데이터 훅을 호출하지 않는다(사용 훅은 `useTranslation`뿐, `frontend/apps/web/src/routes/decisions/DecisionHistoryPage.tsx:22`). 조회 로딩은 하위 패널 소관 | 페이지 수준 로딩 문구는 사실 시트에 없음. 하위 패널 내부 로딩 문구는 미확인 | 없음 | 없음 (페이지 수준). 하위 패널 `ComplianceDecisionLookupPanel`·`EventLineageLookupPanel` 내부는 미확인 (`frontend/apps/web/src/routes/decisions/DecisionHistoryPage.tsx:27`, `frontend/apps/web/src/routes/decisions/DecisionHistoryPage.tsx:28`) |
| 빈 상태 | 페이지 수준 빈 상태 분기 없음(조건 분기 코드 없음, `frontend/apps/web/src/routes/decisions/DecisionHistoryPage.tsx:23-30`) | 페이지 수준 빈 상태 문구 없음. 하위 패널의 조회 전/결과 없음 문구는 미확인 | 없음 | 없음 (페이지 수준). 하위 패널은 미확인 |
| 정상 | 페이지가 렌더링될 때 항상 | `decisions.pageTitle` "결정 이력 뷰어" 제목(`frontend/apps/web/src/routes/decisions/DecisionHistoryPage.tsx:26`) 아래에 판정 조회 패널과 이벤트 계보 조회 패널이 세로로 나란히 표시(`frontend/apps/web/src/routes/decisions/DecisionHistoryPage.tsx:27`, `frontend/apps/web/src/routes/decisions/DecisionHistoryPage.tsx:28`) | 각 패널 안의 조회. 페이지 자체의 버튼·폼 필드는 없음(사실 시트의 폼·버튼 목록 비어 있음) | 구현됨 (`frontend/apps/web/src/routes/decisions/DecisionHistoryPage.tsx:26`, `frontend/apps/web/src/routes/decisions/DecisionHistoryPage.tsx:27`, `frontend/apps/web/src/routes/decisions/DecisionHistoryPage.tsx:28`) |
| 낡은 데이터(신선도 초과) | 신선도·갱신 시각 판단 코드가 페이지에 없음 | 낡은 데이터 안내 문구 없음(사실 시트의 문구 목록에 해당 키 없음) | 없음 | 없음 (페이지 수준). 하위 패널은 미확인 |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J6-DecisionHistoryPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `decisionId` 또는 `positionKey` 입력 후 조회 버튼 클릭 시, 값이 빈 문자열이거나 공백만 있는 경우 | `ComplianceDecisionLookupPanel`: `legacy.complianceDecisionLookupPanel.t4` (`frontend/apps/web/src/routes/compliance/ComplianceDecisionLookupPanel.tsx:36`) / `EventLineageLookupPanel`: `decisions.eventLineage.validationEmpty` (`frontend/apps/web/src/routes/decisions/EventLineageLookupPanel.tsx:35`) — 각 패널 내 `text-danger` 스타일 `<p>`로 표시 | 입력값 수정 후 재조회 | 구현됨 (입력 검증: `ComplianceDecisionLookupPanel.tsx:33-40`, `EventLineageLookupPanel.tsx:32-41`) |
| 인증 필요/세션 만료(401) | API 요청 시 인증 토큰이 만료되거나 유효하지 않을 때 (`shared-types/src/apiError.ts:61-63`) | 없음 (페이지 수준 처리 없음. `protect()` 라우트 가드(`router.tsx:139`)가 인증을 담당하며, 401 응답은 `routeApiError` → `auth_required` 갈래(`shared-types/src/errorRouting.ts:128-129`)로 수렴하나 페이지가 직접 처리하지 않음) | 없음 (페이지 수준 처리 없음) | 없음 |
| 권한 없음/MFA 필요(403) | API 요청 시 테넌트 불일치, 권한 부족, MFA 필요, 정책/리스크 거부 (`shared-types/src/apiError.ts:19-27`) | 없음 (페이지 수준 처리 없음. 하위 패널 `ComplianceActionError`가 `classifyForbidden`→`ForbiddenNotice`(`ComplianceDecisionLookupPanel.tsx:13`)로, `PositionsQueryError`가 동일 갈래(`PositionsQueryError.tsx:24`)로 처리하나 페이지가 직접 처리하지 않음) | 없음 (페이지 수준 처리 없음) | 없음 |
| 충돌/상태 불일치(409) | API 요청 시 중복 요청, 지갑 잔액 정합성 오류, 데이터 수집 미완료, 상태 전환 오류 (`shared-types/src/apiError.ts:29-42`) | 없음 (페이지 수준 처리 없음. `routeApiError`가 409를 `classifyStateConflict`→`refetch_retry`/`invalid_transition`으로 분류(`shared-types/src/errorRouting.ts:111-113`)하나 페이지가 직접 처리하지 않음) | 없음 (페이지 수준 처리 없음) | 없음 |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-DecisionHistoryPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 423(AUTH_ACCOUNT_LOCKED): 세션/계정 잠금. 429(RATE_LIMIT_EXCEEDED): 과부하 | 423: `ErrorMessage` 컴포넌트(`ComplianceDecisionLookupPanel.tsx:61-65`, `ErrorMessage.tsx:57-71`)가 `getApiErrorMessage`로 에러 문구 표시. `classifyRetry`가 `AUTH_ACCOUNT_LOCKED`를 모르므로 `none` → 재시도 버튼 없음(`ErrorMessage.tsx:39`). 429: `ErrorMessage`가 `retryAfterSec` 카운트다운(초 단위, `ErrorMessage.tsx:49-53`) + 재시도 버튼(`ErrorMessage.tsx:66`) | 423: 재시도 버튼 없음(잠금 해제는 별도 프로세스). 429: 카운트다운 후 재시도 버튼 활성화(클릭 시 `ComplianceDecisionLookupPanel.tsx:44`의 `handleLookup` 재호출) | 423: 구현됨(에러 표시만, 재시도 없음 — `ErrorMessage.tsx:39-40`, `ComplianceDecisionLookupPanel.tsx:61-65`, `accountLockout.ts:27-29`). 429: 구현됨(`ErrorMessage.tsx:38-39`, `retryable.ts:27-31`, `errorRouting.ts:119-121) |
| 서버 오류(5xx) | 500(INTERNAL_ERROR), 502(EXCHANGE_FATAL): 재시도 불가. 503(EXCHANGE_UNAVAILABLE, DEPENDENCY_NOT_READY): 재시도 가능 | 500/502: `ErrorMessage`가 에러 문구 + trace_id 노출(`ErrorMessage.tsx:60`). 재시도 버튼 없음(`classifyRetry` → `none`). 503: `ErrorMessage`가 `retryAfterSec` 카운트다운 + 재시도 버튼. | 500/502: 수동 새로고침 또는 운영자 조치. 503: 카운트다운 후 재시도 | 500/502: 구현됨(`serverError.ts:30`, `errorRouting.ts:117`). 503: 구현됨(`serverError.ts:25-28`, `errorRouting.ts:116`) |
| 네트워크 단절 | axios/fetch 실패: `errorCode` 필드 없는 plain `Error`. 모든 분류기(classifyBadRequest/classifyForbidden/classifyRetry/classifyServerError)가 `null`/`serverError.ts:51`의 `not_applicable` 반환 | `ErrorMessage` 컴포넌트가 `getApiErrorMessage(null, error.message)`로 기본 문구 표시(`ErrorMessage.tsx:36`). 재시도 배선은 `onRetry` 전달 여부 — `ComplianceActionError`는 `onRetry`를 받지만 `ErrorMessage.tsx:38-39`의 `classifyRetry`가 plain `Error`에서 `none` → `canRetry=false`. 재시도 버튼 없음. | 수동 새로고침 또는 네트워크 복구 후 재조회 | 구현됨(에러 표시는 됨, 재시도 버튼 없음 — `ErrorMessage.tsx:38-39`, `errorRouting.ts:132`). 재시도 배선 없음(`ComplianceActionError.tsx:11-24`의 `onRetry`가 `canRetry=false`로 무시됨) |
| 부분 실패(일부 데이터만 실패) | `ComplianceDecisionLookupPanel`과 `EventLineageLookupPanel`은 서로 독립적으로 렌더(`DecisionHistoryPage.tsx:27-28`). 한 패널의 실패가 다른 패널에 영향 없음 | 각 패널이 개별 에러 표시. 성공한 패널은 정상 데이터 표시, 실패한 패널은 해당 패널의 에러 컴포넌트 표시 | 각 패널별로 재조회. 페이지 수준 통합 재시도 없음 | 구현됨(패널 독립 렌더 — `DecisionHistoryPage.tsx:27-28`). 페이지 수준 통합 실패 처리 없음(사실 시트의 버튼/핸들러 목록 비어 있음) |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J6-DecisionHistoryPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| TODO | | | |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J6-DecisionHistoryPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J6-DecisionHistoryPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| TODO | | |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J6-DecisionHistoryPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J6-DecisionHistoryPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J6-DecisionHistoryPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
