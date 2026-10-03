# task-11255: frontend CI 적색 재검증

2026-10-04 KST, 대상 c8b498b1582ba9d28d0ae8f9772d4e8a0d9f971a.

## 판정

현재 frontend 단계는 녹색이며 실행 코드 수정은 없다. 조사 시작 시
esc-ci-frontend.json은 이미 resolved였고 resolved_sha는 afd4866febbf였다.
보존된 실패는 npm test rc=1이나 tail에는 React Router future flag 경고만 남아 있다.
TASK에 적힌 stderr 세 줄은 실패 단언이 아니다. 지목된 264587c11은 백엔드 테스트
4개 파일만 변경했다. 과거 실패 원인과 회귀 커밋은 이 자료만으로 확정할 수 없다.
재발 조사에는 실패 실행의 전체 stdout/stderr와 실패 단언이 필요하다.

## 로컬 단계 증빙

local_ci.py frontend 단계의 하위 명령을 현재 worktree에서 실행했다.
설치는 기존 node_modules를 사용했다. 아래 모든 명령은 종료코드 0이다.

| 검증 | 결과 |
| --- | --- |
| 지정된 ReconciliationPage / PortfolioPage.errors / MarketplaceBrowsePage.errors | 3 files, 12 passed, 19.24s |
| npm run lint --workspace=apps/web | 통과, 기존 경고 있음 |
| npm run build --workspace=apps/web | 1,088 modules, Vite 717ms |
| npm test (전 workspace) | 322 files, 3,330 passed |
| web | 201 files, 1,628 passed, 73.25s |
| api-client | 42 files, 501 passed |
| chart-engine | 50 files, 718 passed |
| shared-hooks / shared-types / ui-web | 7 / 421 / 55 passed |
| npm run test:unwired-modules / check:unwired-modules | 10 passed / OK |
| node --test scripts/check_frontend_file_size.test.mjs / npm run check:filesize | 6 passed / OK |
| npm run bench:density | CH-19e OK; panZoom p95 3.037ms, indicatorAdd 5.35ms, tickUpdate p95 0.005ms |
| npm run test:coverage --workspace=apps/web | 201 files, 1,628 passed, 100.49s |
| node scripts/frontend_coverage_ratchet.mjs --baseline <임시 사본> | 91.55% (4,533/4,951), 기존 기준선 91.51%, OK |

check:coverage는 위 두 구성 명령으로 실행했다. 게이트의 자동 기준선 상향을
저장소에 적용하지 않도록 원본 바이트를 복사한 임시 기준선에 대해서만 실행했다.
저장소 기준선, 예산, 허용 오차, 규칙, ignore는 변경하지 않았다.

기존 부정 테스트 3개 이상과 503 실패 주입/재시도 3회 검증이 통과했다.
D2 신규 회귀 및 red 재현: N/A(현재 실패 미재현, 실행 코드 변경 없는 증빙).
성능은 기존 CH-19e 게이트 측정이며 새로운 성능 보장을 추가하지 않는다.
