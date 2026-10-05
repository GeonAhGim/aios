# task-11359: frontend CI 적색 재검증

2026-10-05 KST, 대상 a23b3f25aef3728f5d2e748285487f79b7ca8688.

## 판정

현재 frontend 단계는 녹색이며 실행 코드 수정은 없다. 조사 시작 시 esc-ci-frontend.json은
이미 `status: resolved`였다(sha는 TASK가 지목한 264587c11056eddd9c352d1af807c21139793e7b와
동일). 그 커밋을 `git show --stat`으로 확인한 결과 변경 파일은 아래 4개뿐이며 전부
백엔드 pytest 경로로, frontend/ 디렉터리를 전혀 건드리지 않는다:

```
tests/foundation/providers/test_kis_provider_deepen.py
tests/.../test_migration_fa4_worm_ledger_journal.py
tests/.../test_compute_daily_nav_failure_injection.py
tests/unit/test_ci_child_pytest_environment.py
```

TASK에 적힌 stderr 세 줄(MarketplaceBrowsePage/PortfolioPage 5xx 재시도, IndicatorParityPanel
negative ③)은 vitest가 negative 테스트의 의도된 `console.error`/throw 로그를 stderr로 받아쓴
것이며, 그 자체로 실패 단언이 아니다 — 아래 전체 실행에서 세 파일 모두 통과했다. 이는
task-11255(2026-10-04, esc resolved_sha afd4866febbf)에서 이미 동일하게 확인된 판정이며,
TASK에 반복해 붙은 "[한도 N:NNZ 리셋 대기]" 태그는 그 사이 재시도 과정에서 쌓인 것으로 보인다.
과거 회귀의 원인 커밋은 보존된 로그(경고 tail만 남음)로는 이번에도 확정할 수 없다.

## 로컬 단계 증빙

| 검증 | 결과 |
| --- | --- |
| 지정 3개 파일(MarketplaceBrowsePage.errors / PortfolioPage.errors / IndicatorParityPanel.verifiedGate) | 3 files, 9 passed |
| npm run lint --workspace=apps/web | 통과, 기존 경고만(신규 없음) |
| npm run build --workspace=apps/web | 1,088 modules, 2.03s |
| npx vitest run --workspace=apps/web (전체) | 201 files, 1,628 passed, 131.17s |
| npm test (전 workspace) | web 201/1628, api-client 42/501, chart-engine 53/718, shared-hooks 1/7, shared-types 24/421, ui-web 4/55 — 325 files, 3,330 tests 전부 통과 |
| npm run check:unwired-modules | OK |
| npm run check:filesize | P6 file-size ratchet OK |

저장소 기준선·예산·허용 오차·규칙·ignore는 변경하지 않았다. src/ 수정 없음.
D2 신규 회귀 및 red 재현: N/A(현재 실패 미재현, 실행 코드 변경 없는 재검증 증빙).
