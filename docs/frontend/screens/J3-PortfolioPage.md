# J3-PortfolioPage — `/portfolio`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/PortfolioPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/portfolio` / `frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx`
- 여정 소속: J3


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J3-PortfolioPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 개인 트레이더 (보조 후보 P5 자산운용사는 다중 고객 화면이 [미확인]이라 단정하지 않음; `docs/frontend/PERSONAS_AND_JOURNEYS.md:22`, `:26`) |
| 목적(한 문장) | 총 포트폴리오 가치·미배분 현금·배분 내역을 보고 실행별 배분 자본을 조정(리밸런스)한다 (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:115`, `:137`, `:174`) |
| 진입 조건 | `/portfolio`는 `protect()`로 감싸져 `ProtectedRoute`(로그인 가드)만 거친다 (`frontend/apps/web/src/router.tsx:133`, `:70-72`). `AdminRoute`는 없고 기능 플래그 가드도 이 라우트에는 없다. `ProtectedRoute` 내부 동작은 미확인 |
| 사용자 준비물 | 로그인 세션, 배분된 실행(allocation)이 있어야 표에서 재조정 가능(없으면 빈 상태 안내, `PortfolioPage.tsx:181`). 재조정 입력값은 실행별 새 배분 자본(숫자) (`PortfolioPage.tsx:147`, `:160-165`). 그 외 계좌·권한 요건은 미확인 |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J3-PortfolioPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `usePortfolio` | `frontend/packages/shared-hooks/src/usePortfolio.ts:11` | 마운트 시 1회 조회(`queryKey: ["portfolio"]`, `queryFn: apiClient.getPortfolio()`). `refetchInterval` 없음(1회 조회). 수동 `refetch()` 호출 가능(`PortfolioPage.tsx:53`, `:106`). `useExecutions`는 별 훅으로 5초 폴링(`refetchInterval: 5000`, `usePortfolio.ts:42`) — 포트폴리오 조회에는 적용 안 됨 | `isError` 시 `portfolioError` 설정. `routeApiError()`로 분류(`PortfolioPage.tsx:33` 재사용, `:61`). `EXCHANGE_UNAVAILABLE`/`DEPENDENCY_NOT_READY`(kind=`refetch_retry`/`backoff_retry`) → `ErrorMessage`에 `onRetry=()=>refetch()`(`:106`). `EXCHANGE_FATAL` → `onRetry` 없이 에러 메시지만 표시(`:98-107). `dataUpdatedAt`(react-query 내부 시각)로 신선도 표시, `DataFreshness` 컴포넌트 사용(`:95`). `asOf`는 `null`(PLT-19 봉투 미적용, `:94-95`) |
| `useRebalancePortfolio` | `frontend/packages/shared-hooks/src/usePortfolio.ts:15` | `useMutation`. `mutateAsync({body, idempotencyKey})` 호출(`PortfolioPage.tsx:76`). `onSuccess` 시 `["portfolio"]` 쿼리 키 무효화(`usePortfolio.ts:25`) — 다음 `usePortfolio` 조회/폴링 시 자동 갱신. `refetchInterval` 없음(수정 요청 후 캐시 무효화 방식) | `handleRebalance`의 `catch`에서 처리(`PortfolioPage.tsx:79-82`). `DuplicateSubmitError` → 조용히 무시(`:80`). `ApiError` → `setError(err)` → `RebalanceError` 컴포넌트(`:81`, `:30-42`). `RebalanceError`: `classifyBadRequest` → `BadRequestNotice`(`:31`), `classifyForbidden` → `ForbiddenNotice`(`:32`), 그 외 → `ErrorMessage`(`:33-40`). 성공 시 `rebalance.data.adjusted`/`pendingApproval`로 결과 카운트 표시(`:175-178`) |
| `useIdempotentSubmit` | `frontend/apps/web/src/hooks/useIdempotentSubmit.ts:30` | `submit((key) => action(key))` 호출. `inFlightRef`로 동시 요청 차단(버튼 연타 시 `DuplicateSubmitError` throw, `:36-38`). 성공 시 `manager.discardKey()`로 키 폐기(`:43`). 4xx 실패 시 키 폐기(`:58-59`). 5xx/네트워크 오류 시 같은 키 유지(재시도 가능, `:58`). 키 관리: `createIdempotencyKeyManager()`로 `requestKey: "portfolio.rebalance"` 기반(`:31`, `:40`) | `DuplicateSubmitError` → 호출부(`PortfolioPage.tsx:80`)에서 `return`으로 조용히 종료. 그 외 에러는 재throw → `PortfolioPage.tsx:81`에서 `RebalanceError` 경로로 전달. `classifyIdempotencyFailure`로 `missing_header`(클라이언트 결함) 감지 시 `console.warn`(`:52-56`) — 사용자 화면에 노출 안 함 |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J3-PortfolioPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J3-PortfolioPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J3-PortfolioPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J3-PortfolioPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:173`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J3-PortfolioPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J3-PortfolioPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `Alert` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:5`) | TODO | TODO | TODO |
| `classifyBadRequest` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:31`) | TODO | TODO | TODO |
| `BadRequestNotice` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:31`) | TODO | TODO | TODO |
| `classifyForbidden` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:32`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:32`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:33`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/portfolio/PortfolioPage.tsx:35`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J3-PortfolioPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J3-PortfolioPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J3-PortfolioPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
