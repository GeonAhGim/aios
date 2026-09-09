# ADR-2026-09-09-E: 작업 효율 — 위험도별 검증 깊이, 영향 범위 테스트, 증분 정적 검사

## Status
Accepted (2026-09-09, Chief Architect). 사용자 지시 "토큰뿐 아니라 작업도 효율적으로. 한 워커의 작업에 굳이 전체 테스트를 돌릴 필요는 없다".

## Context
- 지금은 구현 리프 1건마다 QA task 1건 + 리뷰 task 1건이 무조건 따라붙는다(orchestrator make_followup). 문서·테스트만 바꾼 커밋도 3단계를 다 거친다. QA 대기 221건의 근원이다.
- 로컬 CI는 커밋마다 전체 pytest(약 26~45분)·전체 mypy(최대 15분)·openapi(10분)를 돌린다. 하루 수십 커밋이면 CI가 항상 뒤처지고, 적색이 어느 커밋 탓인지 흐려진다.
- QA 워커가 전체 스위트를 돌리다 끝나는 사고가 3회 반복됐다(지침은 이미 "관련 경로만"으로 고침).
- 깊이 하한 D2/D3(ADR-2026-09-09-C)는 유지한다. 효율은 "덜 검증"이 아니라 "위험이 있는 곳에만 검증을 몰아주기"로 얻는다.

## Decision 1 — 위험도 등급별 후속 단계
커밋이 건드린 경로로 등급을 자동 판정한다(orchestrator, 가장 높은 등급 우선).
| 등급 | 경로 | 후속 |
|---|---|---|
| S(안전) | src/services/{order_service,oms,safety,execution_loop}, src/foundation/{ledger,compliance,mandates,risk*}, src/core/risk*, src/db/migrations, src/api/contracts, src/exchanges/*/trading*, src/main.py 조립부 | QA(D3) + 리뷰 전부 |
| M(일반) | 그 외 src/, frontend/packages/chart-engine, frontend api-client | QA(D2), 리뷰는 20% 표본(해시 기반 결정론) |
| L(저위험) | docs/, tests/만, frontend 문구·스타일, C:/aios/pm 프롬프트·yaml, NOTICE·README | QA 생략, 리뷰 10% 표본, 게이트(로컬 CI)로 충분 |
- 같은 축·같은 등급 M의 인접 done 리프 최대 3건은 QA task 1건으로 묶는다(부모 여러 개, 커밋 범위 명시). S는 묶지 않는다.
- 이미 대기 중인 QA/리뷰 task에도 소급 적용한다: L은 "tier L — 게이트 통과로 종결"로 done, M은 재묶음.
- 등급 판정과 표본 추출 결과는 task note에 `tier=S|M|L sample=hit|skip`으로 남겨 감사 가능하게 한다.

## Decision 2 — 로컬 CI: 커밋별 영향 범위 테스트, 전체는 주기적으로
- 커밋별: ruff(변경 파일) + mypy(증분 캐시) + 게이트 전부 + **영향 테스트**(변경 src 모듈 → 같은 경로의 tests 디렉터리 + 그 모듈을 import하는 테스트 파일 grep + 변경된 테스트 파일) 상한 15분. 마이그레이션 변경 시 tests/integration 전부 포함. 등급 S 커밋은 tests/adversarial 전부 포함.
- 전체(pytest 전부 + coverage 래칫 + openapi + replay_verify 24h): 야간 1회, 마지막 전체 이후 커밋 20건 초과 시, 마일스톤 closeout 전, refactor_wide 커밋 후. 결과는 `ci/full/<sha>.json`.
- 커밋별 CI가 녹색이어도 전체가 적색이면 ci_red finding은 전체 결과를 기준으로 낸다(회귀를 놓치지 않기 위해).
- mypy는 워크트리 밖 공용 캐시 디렉터리(`C:/aios/.mypy_cache/<repo>`)로 증분 실행한다. openapi 검사는 src/api 변경 시에만 커밋별로 돈다.

## Decision 3 — 워커 측
- 워커·QA는 관련 경로 테스트만(기존 규칙 유지), 상한 10분. 전체 스위트를 돌리면 실패로 재배정(OPS-18).
- 등급 L의 QA/리뷰 표본과 DEPTH 감사의 문서 대조는 haiku로 돌린다(pools.yaml에 `tier_model: {L: haiku}`).
- 워커 프롬프트에 "큰 diff는 `git show --stat`와 관련 hunk만 본다"를 추가해 컨텍스트를 아낀다.

## Consequences
- 리프당 검증 비용이 3단계 고정에서 등급별 1~3단계로 내려가고, QA 대기가 대략 절반 이하로 준다.
- CI 지연이 커밋당 15분 이내로 묶여 적색 원인 커밋이 분명해진다. 전체 스위트는 하루 1~3회로 줄지만 빠지지 않는다.
- 구현: OPS-22(등급·표본·묶음·소급), OPS-23(영향 테스트·전체 주기·full 리포트), OPS-24(mypy 증분·ruff 변경 파일·openapi 조건), 프롬프트·pools 갱신.
