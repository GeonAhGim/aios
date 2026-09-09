# ADR-2026-09-10-A: 상황 인식 검토 — 검토 컨텍스트 MCP 서버 + 영역별 검토 스킬 + 발견 기억

## Status
Accepted (2026-09-10, CTO). 사용자 제안 "code review 에이전트 스킬이나 MCP 서버로 상황 인식 기반 맞춤형 검토가 더 효율적이지 않나".

## Context
- QA·리뷰 워커는 한 턴짜리 headless Claude이고 정적 프롬프트 하나로 모든 축을 검토한다. 매번 명세·ADR·INVARIANTS·diff를 grep/read로 다시
  모으느라 QA 중앙값 34턴, 긴 실행은 컨텍스트 재읽기 17M 토큰(OPS-29 실측). 같은 지적이 반복돼도 다음 검토는 기억하지 못한다.
- 검토 규칙(tier S/M/L, 깊이 D0~D3, 축별 성능 예산, 불변식)은 문서에 있을 뿐 도구로 제공되지 않아 워커가 매번 해석한다.

## Decision 1 — 검토 컨텍스트 MCP 서버 `aios-review-context` (stdio, Python)
워커가 grep 대신 호출하는 도구:
| 도구 | 반환 |
|---|---|
| task(id) | task JSON + 부모·자식·이전 시도 note |
| spec_row(leaf_id) | 명세 행(DoD 포함)·관련 ADR 목록·불변식 ID |
| change_profile(sha) | 변경 파일·tier(S/M/L)·축·마이그레이션/포트/계약 변경 여부·영향 테스트 목록(OPS-23 선택기 재사용) |
| review_plan(sha) | tier·축·변경 종류로 조립한 맞춤 체크리스트(아래 스킬에서 로드) + 요구 깊이(D2/D3) + 성능 예산 |
| prior_findings(paths) | 같은 파일·같은 축에서 과거 QA/리뷰/DEEPEN/교차 리뷰가 낸 지적(기억 DB) |
| run_impacted_tests(sha, cap_sec) | 영향 테스트만 포그라운드 실행·요약(전체 스위트 금지 강제) |
| gate_status(sha) | 로컬 CI 단계별 결과 |
| record_finding(task, file, kind, severity, text) | 기억 DB에 저장(중복 병합), 반복 3회 이상이면 CONSIST/래칫 후보로 표시 |
- 서버는 C:/aios/pm/mcp/review_context/ 에 두고, worker_runner가 QA·리뷰·DEPTH·교차 리뷰 레인에 `--mcp-config`로 붙인다. Codex 레인에도 같은 서버(MCP 지원)를 붙인다.

## Decision 2 — 영역별 검토 스킬(저장소 `.claude/skills/review-*/SKILL.md`)
- review-safety(주문·리스크·kill switch·컴플라이언스), review-ledger(복식·Decimal·멱등·WORM), review-migration(downgrade·단일 head·expand/contract),
  review-exchange(fail-closed·계약 테스트·재조회 경로), review-dsl(결정론·미래참조·리소스 상한), review-frontend(상태·접근성·계약 대응), review-data(point-in-time·tz·갭),
  review-ops(pm 코드: 라이브 파일 직접 편집 금지·create_task·테스트 격리).
- 각 스킬은 체크리스트 + 반례 예시 + "이 축에서 실제로 났던 사고"(기억 DB에서 생성)로 구성. review_plan이 tier·축에 맞춰 1~3개를 고른다.
- 스킬은 코드와 같은 저장소에 살아 PR로 진화한다. 사고가 나면 그 부류를 스킬 항목 + 기계 검사(CONSIST/래칫)로 동시에 추가한다.

## Decision 3 — 발견 기억(findings DB)
- SQLite(C:/aios/pm/state/findings.db): file, axis, kind, severity, text, task, sha, resolved. 같은 (file, kind)가 3회 이상이면 healthcheck `recurring_finding`
  finding → CONSIST-1 검사 항목 또는 스킬 항목으로 승격하는 task 자동 발행.
- 대시보드에 상위 반복 지적·축별 발견 밀도.

## 기대 효과와 측정
- QA 턴 34→15 이하, 컨텍스트 재읽기 50% 감소, 검토 시간 중앙값 13.7분→7분. 깊이 등급 판정의 일관성(같은 diff를 두 워커가 같은 D로).
- 24h 전후 비교를 OPS-36에서 계측해 note에 남긴다. 효과가 없으면 되돌린다.

## Rejected
- 클라우드 다중 에이전트 리뷰(/code-review ultra)를 자동화에 쓰는 것: 사용자 트리거·과금이라 무인 파이프라인에 맞지 않는다. 마일스톤 closeout 직전 1회 수동 사용은 권장.
- PR 기반 리뷰로 전면 전환: 지연이 커진다. tier S만 XREV(교차 엔진)로 보완한다.

## Consequences
- 구현: OPS-35(MCP 서버), SK-1(스킬 8종), OPS-36(워커 배선·계측), 기억 DB는 OPS-35에 포함.
