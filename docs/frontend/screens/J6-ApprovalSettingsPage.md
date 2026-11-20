# J6-ApprovalSettingsPage — `/settings/approval`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ApprovalSettingsPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/settings/approval` / `frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx`
- 여정 소속: J6


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J6-ApprovalSettingsPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 — 개인 트레이더(투자자). 여정 J6("설정·승인·감사")의 주체. 승인 방식(SOLO/DUAL)을 직접 설정·변경한다. [확인: PERSONAS_AND_JOURNEYS.md §3 표 — J6는 P1(+P2) 중심] |
| 목적(한 문장) | 거래 실행의 승인 방식을 SOLO(1인 승인, 강제 대기 60초) 또는 DUAL(서로 다른 두 계정 순차 서명) 중 하나로 설정하고, DUAL 모드일 때 2차 승인자 연락처를 등록한다. |
| 진입 조건 | `frontend/apps/web/src/router.tsx:148` — `/settings/approval` 라우트는 `protect(<ApprovalSettingsPage />)`로 보호됨. `protect()`는 `ProtectedRoute` 컴포넌트(`frontend/apps/web/src/components/ProtectedRoute.tsx`)를 통과해야 함: (1) 로그인 세션 보유(`router.tsx:70-72`), (2) MFA 활성화(`ProtectedRoute.tsx:32-34`), (3) 위험도 평가 완료(`ProtectedRoute.tsx:35-41`). FeatureFlagGate 미적용(`router.tsx:93-111` 중 없음). |
| 사용자 준비물 | 로그인 세션, MFA 설정 완료, 위험도 평가 완료. DUAL 모드 선택 시 2차 승인자의 연락처 정보(이메일 또는 전화번호). |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J6-ApprovalSettingsPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useApprovalSettings` | `frontend/packages/shared-hooks/src/useAccountSettings.ts:9` | 마운트 시 1회 조회(`useQuery`, `useAccountSettings.ts:10-13`). refetchInterval 없음(`useAccountSettings.ts:10-13` grep 결과 0건). 이후 재조회는 QueryClient 기본값(`staleTime: 0`, `refetchOnWindowFocus: false`)에 의존하므로 미확인. | 페이지는 `data`와 `isLoading`만 구조분해(`ApprovalSettingsPage.tsx:46`). 훅의 `error`는 화면에 렌더링되지 않음(`:97-98`에서 로딩만 처리). `settings`가 `undefined`이면 조건부 렌더링(`:128`)이 false가 되어 폼 기본값(SOLO)으로 그대로 보임. |
| `useUpdateApprovalSettings` | `frontend/packages/shared-hooks/src/useAccountSettings.ts:16` | mutation(`useMutation`, `useAccountSettings.ts:18`). 성공 시 `onSuccess`에서 `["approvalSettings"]` 무효화(`useAccountSettings.ts:20`). HTTP 메서드는 `apiClient.updateApprovalSettings`를 열어 보기 전까지 미확인. | `attemptUpdate` catch(`ApprovalSettingsPage.tsx:62-86`) 처리. `ApiError` + 위험등급 문구(`t8`) 포함 시 `RiskWarningModal`(`:77-80`). 그 외 `ApiError`는 `setError(err)` → `UpdateSettingsError`(`:30-42`)가 400/403/그 외 분기(`:132`). `ApiError` 아님 → `t9` 메시지(`:83-85`). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J6-ApprovalSettingsPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J6-ApprovalSettingsPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-ApprovalSettingsPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J6-ApprovalSettingsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:133`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J6-ApprovalSettingsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J6-ApprovalSettingsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:4`) | TODO | TODO | TODO |
| `classifyForbidden` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:5`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:7`) | TODO | TODO | TODO |
| `BadRequestNotice` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:31`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:32`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:35`) | TODO | TODO | TODO |
| `Alert` (`frontend/apps/web/src/routes/settings/ApprovalSettingsPage.tsx:95`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J6-ApprovalSettingsPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J6-ApprovalSettingsPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J6-ApprovalSettingsPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
