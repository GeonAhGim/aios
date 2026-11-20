# J5-LedgerHistoryPage — `/wallet/ledger`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/LedgerHistoryPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/wallet/ledger` / `frontend/apps/web/src/routes/wallet/LedgerHistoryPage.tsx`
- 여정 소속: J5


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J5-LedgerHistoryPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1(개인 트레이더) — 원장 내역으로 자신의 모든 거래·배분·수수료 이력을 조회. P11(감사자)도 읽기 전용으로 접근 가능(PERSONAS_AND_JOURNEYS.md §2). |
| 목적(한 문장) | 원장(ledger)의 모든 엔트리를 커서 기반 페이지네이션으로 조회하고 이전/다음으로 탐색한다. |
| 진입 조건 | `frontend/apps/web/src/router.tsx:142` — `/wallet/ledger` 경로, `protect()` HOC로 인증 요구. |
| 사용자 준비물 | 로그인된 세션(protect HOC 필요). |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J5-LedgerHistoryPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useCursorPage` | `frontend/apps/web/src/hooks/useCursorPage.ts:25` | 훅 자체는 상태 계산만 하고 네트워크 요청이 없다(25-59행). `next()`/`prev()` 호출 시 `rerender()`로 리렌더링(30·35·41·46행). 폴링 없음. 호출처(`LedgerHistoryPage.tsx:48`)에서 직전 성공 응답의 `meta.page`(45-60행)를 주입받아 cursor를 관리. | 훅 자체는 에러 처리가 없고 `hasNext`/`hasPrev`만 반환(54행). 실제 에러 경로는 호출처에서 `query.isError`(73행)로 분기하여 `ErrorMessage`(74-80행) 표시. |
| `useQuery` | `@tanstack/react-query` (외부 패키지) | `queryKey: ["ledgerHistoryPage", cursorPage.cursor]`, `queryFn: () => fetchPage(cursorPage.cursor)`(50-53행). 폴링 설정 없음 → 폴링 없음. 1회 조회 후 이전/다음 버튼(87·89행) 클릭 시 cursor 변경 → 새 queryKey로 재조회. 수동 새로고침은 `ErrorMessage.onRetry`(79행)의 `query.refetch()`만 있음. | 기본 `fetchPage`(28-29행)는 항상 reject하며 메시지 "원장 내역 조회 API가 아직 제공되지 않습니다." 오류는 `query.isError`(73행)에서 `ErrorMessage`(74-80행)로 표시, 목록(82행)과 페이지 버튼(85행) 숨김. 재시도 가능 여부: `routeApiError`(62행) 결과의 `kind`가 `refetch_retry` 또는 `backoff_retry`일 때 `onRetry` 활성화(79행). `routeApiError` 내부 결정 로직은 미확인. |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J5-LedgerHistoryPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `query.isLoading`이 true — `LedgerHistoryPage.tsx:82`에서 `LedgerEntryList`에 `isLoading={true}` 전달 | `LedgerEntryList`가 `<LoadingState />` 렌더(회색 로딩 인디케이터) | 사용자 상호작용 비활성화(로딩 중에는 버튼도 `disabled` 상태) | 구현됨 — `frontend/apps/web/src/components/LedgerEntryList.tsx:115-116` |
| 빈 상태 | `query.data`가 있고 `entries.length === 0`, `query.isError` false — `LedgerHistoryPage.tsx:82`의 entries prop이 빈 배열 | `<EmptyState>` 컴포넌트 표시, 문구 키 `legacy.ledgerEntryList.t10` | "이전" 버튼(`cursorPage.hasPrev`일 때 활성화, `LedgerHistoryPage.tsx:87-88`), "다음" 버튼(`cursorPage.hasNext`일 때 활성화, `LedgerHistoryPage.tsx:89-90`) | 구현됨 — `frontend/apps/web/src/components/LedgerEntryList.tsx:119-120` |
| 정상 | `query.data`가 있고 `entries`가 비어 있지 않으며 `query.isError` false | `<PageHeader>` 제목 `legacy.ledgerHistoryPage.title1`("원장 내역", `LedgerHistoryPage.tsx:69`), `<DataFreshness>`로 `as_of` 기준 시각·경과 시간 표시(70), `<LedgerEntryList>`가 각 엔트리를 `<JournalEntryCard>`로 렌더(82), 하단에 페이지네이션 버튼(85-92) | "이전" 버튼 → `cursorPage.prev` 호출 → cursor 변경 → `useQuery`가 새 `queryKey`로 재조회(87-88), "다음" 버튼 → `cursorPage.next` 동일 흐름(89-90) | 구현됨 — `frontend/apps/web/src/routes/wallet/LedgerHistoryPage.tsx:68-92`, `frontend/apps/web/src/hooks/useCursorPage.ts:32-42` |
| 낡은 데이터(신선도 초과) | `DataFreshness` 내부 `deriveFreshness`가 `isStale: true` 반환 — `ageSec >= staleAfterSec`일 때(`frontend/packages/api-client/src/envelope.ts:127`), 기본 `staleAfterSec=300`(`frontend/apps/web/src/components/DataFreshness.tsx:20`) | `DataFreshness`가 `legacy.dataFreshness.t2`("N분 전") 텍스트와 `legacy.dataFreshness.t3`("낡음") 경고 배지 렌더(`frontend/apps/web/src/components/DataFreshness.tsx:32-37`) | 명시적 새로고침 버튼 없음. "이전"/"다음" 페이지네이션 시 새 queryKey로 자동 재조회. 에러 표시 컴포넌트의 재시도 핸들러는 에러 상태에서만 표시되므로 신선도 초과 시에는 동작하지 않음 | 구현됨 — `frontend/apps/web/src/components/DataFreshness.tsx:34-36`, `frontend/packages/api-client/src/envelope.ts:127` |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J5-LedgerHistoryPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J5-LedgerHistoryPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J5-LedgerHistoryPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/wallet/LedgerHistoryPage.tsx:87`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/wallet/LedgerHistoryPage.tsx:89`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J5-LedgerHistoryPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J5-LedgerHistoryPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `routeApiError` (`frontend/apps/web/src/routes/wallet/LedgerHistoryPage.tsx:62`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/wallet/LedgerHistoryPage.tsx:74`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J5-LedgerHistoryPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J5-LedgerHistoryPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J5-LedgerHistoryPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
