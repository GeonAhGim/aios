# J1-RiskAssessmentPage — `/onboarding/risk-assessment`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/RiskAssessmentPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/onboarding/risk-assessment` / `frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-RiskAssessmentPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더/투자자) — `docs/frontend/PERSONAS_AND_JOURNEYS.md:22`: "전략을 만들거나 고르고, 검증하고, 모의->실운용하며 감독" [확인] |
| 목적(한 문장) | 회원가입(MFA 완료) 후 투자자 적합성평가를 통해 투자 경험·위험 감내도·투자 목표·자금 필요 시점을 수집하고, 이에 기반한 위험 등급을 안내한다. [확인: RiskAssessmentPage.tsx:142-144] |
| 진입 조건 | `router.tsx:88` — `/onboarding/risk-assessment` 라우트는 `protect()`로 ProtectedRoute 감싸짐. `ProtectedRoute.tsx:35-41` — `me.mfaEnabled=true`이고 `riskProfile`이 없을 때 강제 리다이렉트. 즉 MFA 완료 후 최초 진입 [확인] |
| 사용자 준비물 | MFA(2단계 인증) 완료 — `MfaSetupPage.tsx:72`에서 검증 성공 시 `/onboarding/risk-assessment`로 자동 전이 [확인] |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J1-RiskAssessmentPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useSubmitRiskAssessment` | `frontend/packages/shared-hooks/src/useSuitability.ts:15` | `useMutation` — 폼 제출(`RiskAssessmentPage.tsx:110`) 시 1회 POST `/api/v1/account/risk-assessment`(`frontend/packages/api-client/src/clients/account.ts:26`). `mutateAsync`로 동기 호출, 성공 시 `onSuccess` 훅(`useSuitability.ts:19`)이 `queryKey: ["riskProfile"]` 무효화. 폴링 없음(단일 POST). | `handleSubmit` catch(`RiskAssessmentPage.tsx:119`): `ApiError`면 그대로 `setError`, 아니면 `t("legacy.riskAssessmentPage.t12")`로 감싸서 `setError`. `SubmitError` 컴포넌트(`RiskAssessmentPage.tsx:64-76`)가 `classifyBadRequest`→`BadRequestNotice`, `classifyForbidden`→`ForbiddenNotice`, 나머지는 `routeApiError`→`ErrorMessage`(trace_id, retry_after 지원). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-RiskAssessmentPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩(초기) | 없음. 이 화면은 서버 조회를 하지 않는다. `useRiskProfile`은 `frontend/packages/shared-hooks/src/useSuitability.ts:6-13`에 정의만 있고 이 파일에서 쓰지 않음. | 로딩 스피너나 대기 UI 없음. 폼이 바로 렌더링된다. | — | 없음 |
| 로딩(제출 중) | `submit.isPending`이 true (`useSubmitRiskAssessment`의 mutation state). | 제출 버튼에 스피너 표시. 버튼 문구는 `legacy.riskAssessmentPage.t11` "제출하기" (`:203`). 입력 필드는 제출 중에도 `disabled`가 없어 수정 가능 (`:149-199`). | — | 미확인 (ui-web Button의 loading 스피너 시각 표현 확인 안 함) |
| 빈 상태 | 폼에 사용자가 입력하지 않은 상태. | 빈 상태 UI 없음('없음'). 대신 기본값이 모두 세팅됨: 경력 0년 (`:82`), 투자 가능 비중 10% (`:83`), 감내 손실 10% (`:84`), `LONG_TERM_GROWTH` (`:86`), `WITHIN_1_YEAR` (`:89`). 연수 입력은 `Number(e.target.value)` (`:154`)라 빈 문자열이 0이 됨. | 기본값으로 채워진 폼을 수정 후 제출 가능. | 없음(빈 상태 UI 없음 — 기본값으로 대체) |
| 정상 | 폼에 값이 입력된 상태. | 제목 `t1` "투자자 적합성평가 (필수)" (`:142`), 설명 `t2` ("투자 경험과 위험 감내도를 바탕으로 맞는 위험등급을 안내해드립니다. 강제 차단이 아니라 참고용 조언·불일치 경고 목적으로만 사용됩니다.") (`:144`), 라벨 `label3` "투자 경험 연수" (`:148`)·`label4` "투자 목표" (`:180`)·`label7` "자금 필요 시점" (`:190`), 선택지 `t5` "단기 수익 추구"·`t6` "장기 자산 성장" (`:185-186`), `t8` "1년 이내"·`t9` "1~3년"·`t10` "3년 이상" (`:195-197`), 제출 버튼 `t11` "제출하기" (`:203`). | 제출 성공 시 `clearDraft()` 후 `navigate("/dashboard")` (`:117-118`). 뒤로가기 링크 `backLink` "← 이전 단계(2단계 인증)로 돌아가기" → `/onboarding/mfa-setup` (`:127-131`). | 구현됨 (`:142-144`, `:148`, `:180`, `:185-186`, `:190`, `:195-197`, `:203`, `:117-118`, `:127-131`) |
| 낡은 데이터(신선도 초과) | 신선도 기준 없음. `readDraft()` (`:33-41`)는 타임스탬프나 만료 시간이 없다. | sessionStorage 초안을 복원한다 (`:81`, `:92`). 복원 안내 `restoredNotice` "세션 만료로 중단됐던 입력값을 복원했습니다." (`:135`)는 초안이 존재하기만 하면 표시됨 (`:133` `restoredFromDraft`). | 복원된 초안 값을 수정 후 제출 가능. 초안은 제출 성공 시에만 지워진다 (`:117`). | 구현됨 (`:33-41`, `:81`, `:92`, `:133-137`). 갭: 문구의 '세션 만료' 조건과 실제 조건(초안 존재 시 모두 표시)이 다름 |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J1-RiskAssessmentPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-RiskAssessmentPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-RiskAssessmentPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx:202`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-RiskAssessmentPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-RiskAssessmentPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx:4`) | TODO | TODO | TODO |
| `classifyForbidden` (`frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx:5`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx:6`) | TODO | TODO | TODO |
| `BadRequestNotice` (`frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx:65`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx:66`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx:69`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-RiskAssessmentPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-RiskAssessmentPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-RiskAssessmentPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
