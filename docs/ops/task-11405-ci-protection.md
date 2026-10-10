# task-11405 CI 보호 재검토·인수인계

2026-10-09, 기준 앱 SHA `c1d3bf491fd98eab4e812d50dc9325957bb004ff`.

## 원격 증거와 CODEOWNERS 경계

- `gh api repos/GeonAhGim/aios`: archived=false, default_branch=main. 현재 origin이다.
- `gh api repos/GeonAhGim/mihwa-aios`: archived=true. 이전 보관 저장소의 403을 현재 origin의 차단 사유로 재사용하지 않는다.
- `gh api repos/GeonAhGim/aios/contents/CODEOWNERS`: 루트 파일 존재. `.github/CODEOWNERS`는 404지만 CODEOWNERS 부재를 뜻하지 않는다.
- `gh api repos/GeonAhGim/aios/collaborators`: GeonAhGim, role_name=admin만 확인. 임의 팀·리뷰어를 지정하지 않는다.
- 기존 소유권은 kernel policy/permission, core strategy/portfolio/risk/executor/safety, foundation, exchanges, api, services, migrations, frontend, docs/design에 한정됐다.
- 이번 변경은 `* @GeonAhGim` 기본 규칙으로 `.github/workflows/quality.yml`, CODEOWNERS 자체, tests, scripts, contracts, 의존성·기준선 파일까지 포괄한다. 기존 구체 규칙과 FROZEN 승인은 별개다.
- 기존 주석의 private/free-plan 제한은 현재 원인으로 입증되지 않아 제거한다.

## PR 게이트와 사람이 적용할 설정

- quality.yml은 필터 없는 pull_request 트리거이며 verify, guards, frontend jobs에 PR 제외 조건이 없다. workflow 수정은 불필요하다.
- required check 후보: `verify`, `guards`, `frontend` (Quality Gate). 실제 PR check-run 이름과 GitHub Actions 발행자를 확인한 뒤 필수로 지정한다. 기준 main의 check-runs 조회는 빈 목록이었다.
- verify: secret scan, pip audit, lint, mypy, zone/BOM/compliance/child-order/perf-marker 검사, 일반 테스트, coverage ratchet. 기존 perf serial 단계의 continue-on-error=true는 이번 변경 전부터 존재하며 그대로 기록한다. 모든 단계가 필수라고 주장하지 않는다.
- guards: META_GUARDS_REF 없으면 실패. 원격 변수는 `43f9f94a6e81cf1620e5412edae1094e0189e731`로 확인했다.
- frontend: npm audit(high), lint/build, 조건부 vitest 및 coverage ratchet. 조건부 실행을 무조건적인 테스트 보장으로 간주하지 않는다.
- `GET /repos/GeonAhGim/aios/branches/main/protection`: HTTP 404, Branch not protected. `GET /repos/GeonAhGim/aios/rulesets`: []. 원격 보호가 켜져 있다고 주장하지 않는다.
- 사람 작업: main 대상 PR 필수, code-owner review 필수, 승인 최소 1건, 새 커밋 시 낡은 승인 무효화, required checks 위 3개 및 최신 base 요구, 관리자 포함 우회 금지, force-push/deletion 금지, 대화 해결 필수 설정·검증.
- 현재 유일한 확인 소유자와 에이전트 PR 작성 계정이 같다. 독립 리뷰어의 write 권한과 CODEOWNERS 등록을 사람이 결정해야 한다. 같은 계정의 웹 로그인으로 자기 PR 승인 문제가 해결된다고 가정하지 않는다. 설정 API를 에이전트가 변경하지 않았다.

## fleet 3중 방어 및 parity

- pre_push_gate.py: baseline 수치 증가를 find_baseline_regressions로 검사해 push 전에 거부.
- local_ci.py: 동일 축을 baseline_guard 결과와 전체 ok에 반영.
- healthcheck.py: check_baseline_raised로 이미 유입된 증가를 감시. 사전 차단과 사후 탐지를 구분한다.
- ci_recheck.build_steps가 실행 목록을 만들고 gate_spec.py가 mypy/ruff/pytest 대상 선정 규칙을 공유한다. healthcheck.check_ci_parity_drift는 ci_parity.find_drift를 소비한다.
- 제공된 Python으로 fleet tests/test_gate_spec_parity.py, test_gate_spec_pm_repo.py, test_baseline_guard.py, test_ci_parity_drift.py를 실행: 72 passed (2.38s). 실패 주입·누락/깨진 workflow의 적색 탐지 회귀 포함.
- 현재 앱 checkout을 인자로 ci_parity.find_drift 실행: []. 이전 기록의 Perf marker guard/Test(perf,serial) 누락 2건은 재현되지 않는다. 이는 실행 목록 대조 결과이며 전체 CI 성공 증거는 아니다.
- DB URL이 없어 앱 DB 통합·replay_verify를 실행하지 않았다. 실행 경로 변경 없음으로 성능/D3 검증은 N/A. 전체 pytest는 실행하지 않았다.

## 변경·복구·남은 결정

- 변경 경로: CODEOWNERS, 이 인수인계 문서. 게이트·단언·기준선·FROZEN/실거래 코드 변경 없음.
- 보존 attempt4 패치는 tests/conftest.py, tests/integration/test_metrics_collector.py 및 order residue 산출물, unpushed 패치는 backtest sandbox 변경이다. TASK 범위와 달라 적용하지 않았다. 기존 미추적 파일도 보존했다.
- 이전 9bfb38fb는 현재 저장소에서 해석되지 않는다. 현재 반영 완료 SHA로 보고하지 않는다.
- 실패 시 PR을 병합하지 않는다. 병합 후 리뷰 라우팅에 문제가 있으면 이 커밋을 revert한다. 원격 보호 설정은 별도 사람 변경이므로 코드 revert와 분리해 처리한다.
- fleet 코드 변경·push가 없어 fleet_deploy 대상이 아니다. 앱 PR과 사람 설정이 완료되기 전 status=needs_decision으로 인계한다.
