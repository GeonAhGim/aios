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
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-DecisionHistoryPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
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
