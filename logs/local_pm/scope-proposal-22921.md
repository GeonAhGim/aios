# task-22921 파일 범위 분할 제안 (task-22930)

## 실제 저장소 확인
- task-22921 files=`frontend/e2e/`(git 추적 12개: spec 8 + support 4), `frontend/apps/web/src/`(추적 412개). 디렉터리 범위라 local-pm이 해석 불가(unresolved_path_scope).
- 리뷰 발견의 실제 대상은 `frontend/playwright.config.ts` 단 1개(`webServer.startServerIfRequired: true`, 79행). 이 옵션은 Playwright webServer의 공식 옵션이 아니다(공식: command/url/port/reuseExistingServer/timeout/env/cwd/stdout/stderr/ignoreHTTPSErrors/gracefulShutdown). 즉 무효 옵션이며, `reuseExistingServer:false`가 이미 매번 기동하므로 제거해도 동작 불변이다. 포트 5173 하드코딩은 없다(WORKTREE_PORT 파생).
- 발견 (2) "10/10 녹색 재현 증빙 없음"은 코드 수정이 아니라 e2e 실행 증빙 문제다.

## 제안 분할 (files를 실제 파일로 교체)
1. 리프 A (frontend, 코드): files=[`frontend/playwright.config.ts`] — 무효 옵션 `startServerIfRequired` 한 줄 제거. 검증: `npx playwright test --list` 구성 로드 + 프론트 게이트.
2. 리프 B (qa/frontend, 증빙): files=[`frontend/e2e/journey-j3-paper-order-to-position.spec.ts`, `frontend/e2e/support/mockBackend.ts`] 읽기 전용 — journeys 반복 실행 결과를 note에만 기록(코드 변경 없음). 빌드·dev 기동이 필요해 단독 리프로 분리.
3. `frontend/apps/web/src/`(412개)와 `frontend/e2e/` 나머지는 이 finding과 무관 — 범위에서 제외.

원본 task-22921의 hold 해제·재큐·코드 수정은 하지 않았다.
