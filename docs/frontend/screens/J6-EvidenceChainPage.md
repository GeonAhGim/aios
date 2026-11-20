# J6-EvidenceChainPage — `/admin/evidence-chain`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/EvidenceChainPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/admin/evidence-chain` / `frontend/apps/web/src/routes/admin/EvidenceChainPage.tsx`
- 여정 소속: J6


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J6-EvidenceChainPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P8(운영자) — `docs/frontend/PERSONAS_AND_JOURNEYS.md:29` P8: 가입 승인·분쟁·안전장치·정합성 점검. P11(감사자·컴플라이언스)도 읽기 전용 접근. [확인: `:29`(P8), `:32`(P11), `router.tsx:176` admin 라우트] |
| 목적(한 문장) | 증빙 감사 체인의 무결성을 검증(AUD-003)하고, 타임라인 이벤트를 페이지네이션으로 조회한다. [확인: `EvidenceChainPage.tsx:65-68` title/t2, `useEvidence.ts:15,22`] |
| 진입 조건 | `/admin/evidence-chain` 경로에 접근. `router.tsx:176`에서 `protectAdmin(<EvidenceChainPage />)` 가드: `ProtectedRoute`(로그인 필수) + `AdminRoute`(관리자 권한 필수) 이중 적용. [확인: `router.tsx:74-79`, `:176`] |
| 사용자 준비물 | 테넌트 ID (선택사항, 비워두면 system 이벤트만 검증). `placeholder4` 입력 필드. [확인: `EvidenceChainPage.tsx:72-74`, `t3` 설명 문구] |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J6-EvidenceChainPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useCursorPage` | `frontend/apps/web/src/hooks/useCursorPage.ts:25` | 네비게이션 상태 훅(비 네트워크). `meta`(next_cursor)를 props로 받아 `next()`/`prev()`/`reset()` 콜백 제공. `meta`가 null이면 `next()`는 no-op(line 33). `hasNext`는 `meta != null && meta.next_cursor != null`로 결정(line 54). EvidenceChainPage.tsx:42-43에서 `committed` 상태의 `nextCursor`를 `meta`로 전달. | 네트워크 호출 없음. `meta` 누락 시 `hasNext=false`, `next()` no-op. |
| `useAuditTimeline` | `frontend/packages/shared-hooks/src/useEvidence.ts:15` | React Query `useQuery`. `queryKey: ["auditTimeline", params]`(line 17). 폴링 설정 없음 — 마운트 시 1회 조회. `cursor`(pager.cursor) + `limit: 20` 파라미터(line 44). | `timeline.isError` 시 페이지에 별도 렌더 없음 — `timeline.data`가 undefined이므로 `items=[]` → `EmptyState` 표시(EvidenceChainPage.tsx:60, 119). `EvidenceActionError`는 `verify` 훅 전용(line 89). 403 처리가 누락됨. |
| `useVerifyAuditChain` | `frontend/packages/shared-hooks/src/useEvidence.ts:22` | React Query `useMutation`(line 23). `mutationFn: (tenantId) => apiClient.verifyAuditChain(tenantId)`. 사용자 버튼 클릭 시 `verify.mutate(tenantId.trim() || undefined)`로 수동 트리거(EvidenceChainPage.tsx:57). `isPending`으로 버튼 로딩 표시(line 79). | `verify.isError` 시 `EvidenceActionError(verify.error)` 렌더(EvidenceChainPage.tsx:87-91). 403 → `ForbiddenNotice`, 그 외 → `ErrorMessage`(traceId+retryAfterSec). 409 ChainIntegrityError는 페이지에서 재계산하지 않음(주석: line 36-38). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J6-EvidenceChainPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J6-EvidenceChainPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-EvidenceChainPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J6-EvidenceChainPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/admin/EvidenceChainPage.tsx:79`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/admin/EvidenceChainPage.tsx:123`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/admin/EvidenceChainPage.tsx:131`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J6-EvidenceChainPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J6-EvidenceChainPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/admin/EvidenceChainPage.tsx:18`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/admin/EvidenceChainPage.tsx:18`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/admin/EvidenceChainPage.tsx:19`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/admin/EvidenceChainPage.tsx:21`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J6-EvidenceChainPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J6-EvidenceChainPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J6-EvidenceChainPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
