# task-11182: frontend CI 재검증

2026-10-02, 검증 대상 `dc26865879e2df7a51abc69ccaa5d7879d349985`.

## 결과

- `npm run lint --workspace=apps/web`: 종료코드 0. 기존 경고는 남아 있음.
- `npm run build --workspace=apps/web`: 종료코드 0, 1,088개 모듈, Vite 빌드 4.13초.
- `npm run test:coverage --workspace=apps/web`: 종료코드 0, 201개 파일 / 1,628개 테스트 통과, 195.04초.
- 라인 커버리지 91.55% (4,533 / 4,951), 기존 기준선 91.51%.
- `node scripts/frontend_coverage_ratchet.mjs --baseline <임시 기준선 사본>`: 종료코드 0.
  저장소 기준선의 바이트를 그대로 복사해 실행했다. 도구의 자동 상향은 임시 사본에만 적용되며
  저장소 기준선, 허용 오차, 테스트 예산, ignore는 변경하지 않았다.

## 판정과 한계

`esc-ci-frontend.json`의 실패 단계는 `coverage_ratchet rc=1`이다. TASK에 인용된
ReconciliationPage / PortfolioPage / MarketplaceBrowsePage의 stderr는 React Router의
향후 버전 경고이며, 그 자체로 테스트 실패 증거는 아니다. 현재 커버리지 실행은 이 세 파일을
포함해 모두 통과했다. 에스컬레이션은 검증 시작 전 이미 `resolved`였으며,
resolution은 `최신 CI 녹색 bc19e0a9c01b`였다.

남아 있는 원본 로그에는 실패 단언이나 커버리지 최종 수치가 없으므로 과거 실패의 근본 원인과
회귀 커밋을 확정할 수 없다. 현재 재현되지 않는 결함에 추측성 코드를 추가하지 않는다.
이 커밋은 수정 커밋이 아닌 재검증 증빙이다. 이전 보존 패치의 기준선 상향 및 검증되지 않은
global 복원 변경은 적용하지 않았다. 재발 시 잘리지 않은 실패 단계 stdout/stderr와 종료코드가 필요하다.

D2 추가 증빙: N/A(실행 코드 변경 없는 재검증 기록). 기존 부정 테스트와 5xx 실패 주입 테스트는
위 커버리지 실행에 포함된다. 성능 예산 변경이나 새로운 성능 보장을 주장하지 않는다.
