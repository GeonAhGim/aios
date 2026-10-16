# task-11563 CI 보호 인수인계

2026-10-16 재검증. 기준 HEAD: 93ea5a4990d3e5605127d7eb11a2f1e8ad7c049b.

## 앱 소유권과 PR 게이트
- GitHub API repos/GeonAhGim/aios: owner=GeonAhGim, archived=false, main. origin은 aios이며 구 mihwa-aios는 archived=true.
- collaborators 조회에서 GeonAhGim(admin)만 확인. 루트 CODEOWNERS가 이미 존재하지만 CI/tests/scripts/contracts/의존성/기준선 및 자기 파일은 기존 zone 규칙에서 빠졌다.
- 기본 규칙 `* @GeonAhGim`을 추가하고 기존 zone 규칙을 유지한다. 임의 소유자 없음. FROZEN 승인과 무관하다.
- quality.yml은 필터 없는 pull_request 트리거. verify/guards/frontend 모두 PR 제외 조건 없음. 기존 workflow 수정 불필요.
- required check 후보: verify, guards, frontend. verify는 secret scan, 공급망, lint/mypy, zone/BOM/compliance/child-order/perf-marker, 일반 테스트와 coverage ratchet을 실행한다.
- 기존 perf serial은 continue-on-error=true. frontend vitest/coverage는 조건부다. 이를 필수 실행으로 과장하지 않으며 이번 작업에서 완화하거나 baseline을 올리지 않는다.

## 원격 보호: 사람 작업 HB-11563
- GET branches/main/protection 실측: required verify/guards(app_id=15368), strict=true, enforce_admins=false, conversation resolution=true, force-push/deletion=false. required_pull_request_reviews 필드는 없음. GET rulesets=[] .
- 10월 12일 보존 기록의 승인 1명/code-owner review/admin 강제 설정과 현재 값이 다르다. 과거 관측을 현재 증거로 재사용하지 않는다.
- 사람이 main의 PR 필수, code-owner review, 승인 최소 1명, stale approval 폐기, 마지막 push 독립 승인, 관리자 포함 우회 차단을 설정/검증해야 한다. frontend 필수 추가도 사람 결정이다. verify/guards와 strict=true는 유지한다.
- 독립 리뷰어의 write 권한과 CODEOWNERS 등록을 사람이 결정해야 한다. 현재 확인된 계정 하나로 자기 PR 승인 가능하다고 가정하지 않는다.
- 원격 보호 설정은 변경하지 않았다. 이 문서/소유권 변경은 설정 완료를 대신하지 않는다.

## fleet 3중 방어와 parity
- .claude/hooks/pre_push_gate.py find_baseline_regressions: push 전 baseline 증가 차단.
- local_ci.py find_baseline_regressions/baseline_guard: CI 결과 반영.
- healthcheck.py check_baseline_raised: 유입 후 탐지. 사전 차단과 구별한다.
- ci_recheck.build_steps는 gate_spec의 ruff 대상과 mypy 범위를 공유하며 healthcheck.check_ci_parity_drift는 ci_parity.find_drift를 사용한다.
- 현재 앱 경로의 ci_parity.find_drift 결과=[] . 실행 목록 대조이며 전체 CI 통과 증거는 아니다.
- 선행 task-11880 완료 commit 5b2b6d00. scanner 부재 테스트 resolve_gitleaks 경계 격리가 반영됐다.
- 검증 결과는 아래 최종 기록 및 task.tests에 남긴다. fleet 코드는 수정하지 않는다.

## 변경과 실패/롤백
- 변경: CODEOWNERS, 이 문서. workflow/단언/baseline/FROZEN/실거래 경로 변경 없음.
- 음성/실패 주입/적색 재현: fleet 6개 기존 회귀 파일의 scanner 부재, baseline 증가, workflow 단계 누락 검증을 재실행한다.
- 성능 수치/D3/replay_verify: N/A(실행 로직을 바꾸지 않는 소유권 및 인수인계 변경).
- 실패 시 병합 중지. 착지 후 소유권 라우팅 문제가 생기면 해당 커밋 revert. 원격 설정 복구는 별도 사람 작업.
- 작은 PR 1개로 검토하며 원격 설정 미완료는 HB-11563으로 추적한다.

## 최종 로컬 검증
- fleet 6파일: 129 passed (13.59s). scanner 부재 3건 포함 모두 통과.
- 앱 ruff PASS, mypy PASS(1569 source files), zone PASS. parity=[] .
