# ADR-2026-09-09-D: 변곡점 프로토콜과 누수 방지 — 마일스톤 종결·대규모 리팩터링·외부 변화를 기계가 먼저 잡는다

## Status
Accepted (2026-09-09, Chief Architect). 사용자 지시 "예정 작업이 끝나거나 대규모 리팩터링·변곡점이 예상되면 사전 조치해 누수를 막고, 결과를 100% 클리어하게".

## Context
지금까지의 누수는 모두 "전환 순간"에 생겼다: 한도 창 전환 때 작업 유실, stash pop으로 라이브 파일 파손, task id 덮임, 워커가 남긴
"후속 과제" 메모가 task로 이어지지 않음, 명세 행 추가가 task 없이 방치, QA가 done을 찍었지만 깊이가 얕음. 변곡점은 예측 가능하므로
사람이 아니라 healthcheck·orchestrator가 먼저 감지하고, PM이 정해진 절차를 밟게 한다.

## Decision 1 — 변곡점 종류와 자동 감지(healthcheck finding)
| 변곡점 | 감지 신호 | finding |
|---|---|---|
| 마일스톤 종결 임박 | milestones.yaml에 정의된 task 집합의 미완료 ≤ 5건 | milestone_closing (medium) |
| 마일스톤 종결 가능 | 위 집합 전부 done + closeout_check 녹색 | milestone_ready (high, PM이 종결 절차 수행) |
| 대규모 리팩터링 | 한 커밋이 40파일 이상 또는 마이그레이션+포트 서명 동시 변경, 또는 task spec에 "리팩터/재구성/마이그레이션 v2" | refactor_wide (high) — ADR 참조 없으면 CI 적색 |
| 외부 계약 변화 | 거래소 API 버전·라이브러리 major 상향·Python 버전·모델/엔진 교체 | external_change (medium) — 계약 테스트 전수 재실행 |
| 실자금 전환 | HB-5 상태 변경 | live_transition (high) — 별도 체크리스트 |
| 한도·장애 창 | model_limits/network_outage 기록 | 기존 finding 유지, 종료 후 orphan 스캔 |

## Decision 2 — 누수 스캐너(매 5분)
- **고아 산출물**: 워커 워크트리의 미커밋 변경·stash·`origin/wt/*` 미머지 브랜치가 해당 task done 이후에도 남아 있으면 finding + 자동 task("고아 산출물 회수 <id>").
- **미이행 후속**: done task의 note에 "후속|추후|나중|deferred|TODO|follow-up|별도 task" 패턴이 있는데 그 task를 parent로 하는 후속 task가 없으면 자동으로 후속 task 생성(spec = 해당 note 문장, 우선 3). 스캔 결과는 note에 `leak_scanned=1`로 표시해 중복 방지.
- **코드 래칫**: `pytest.mark.skip/xfail`, `TODO|FIXME|XXX`, `NotImplementedError`, `type: ignore`(기존) 개수 baseline 파일 — 증가하면 CI 적색. 감소는 baseline 자동 갱신.
- **결정 노화**: needs_decision 2시간 초과(기존 decision_stale), human_blocked open 항목 7일마다 재검토 finding.
- **명세 커버리지·id 덮임**: ADR-2026-09-09-C(OPS-17).

## Decision 3 — 마일스톤 종결 절차(PM, 사람 없이)
1. milestone_ready finding 확인 → `scripts/closeout_check.py <milestone>` 실행: 종료조건 각 항을 기계 검사(테스트 존재+통과 마커, CI 녹색, 깊이 감사 문서에 D2 미만 0, spec_coverage 0, needs_decision 0, orphan 0, 래칫 baseline 대비 증가 0, human_blocked에서 해당 마일스톤 차단 항목 0).
2. 전부 녹색이면 `docs/milestones/<M>_CLOSEOUT.md` 생성(각 항 증빙 링크: 테스트 경로·CI sha·감사 문서), git tag `milestone/<M>`, DB 스키마 스냅샷·백업 1회.
3. 다음 단계 릴리스: hold task(M2-HOLD 등) 해제는 closeout 파일이 있어야만 가능(orchestrator hold_gate = closeout 파일 존재).
4. 하나라도 적색이면 항목별 task 발행 후 다시 1로.

## Decision 4 — 대규모 리팩터링 절차
- 사전: ADR 필수(범위·롤백·대체 경로), 영향 경로 목록으로 **경로 잠금**(같은 파일을 만지는 다른 task는 orchestrator가 hold), git tag `pre-refactor/<name>`, 테스트 DB 스냅샷.
- 도중: 리팩터링 task는 단독 실행(같은 role 다른 워커에 겹치는 경로 배정 금지), 커밋 메시지에 ADR id, 매 커밋 replay_verify.
- 사후: 전체 회귀(로컬 CI) + 계약 테스트 전수 + 깊이 감사 해당 축 재실행 + 성능 예산 재측정. 전부 녹색 후 잠금 해제.

## Decision 5 — 외부 변화 절차
- 거래소 API/라이브러리 major/Python 상향: 계약 테스트 전수 + 어댑터별 fail-closed 검토 + `docs/exchanges/*_GAPS.md` 갱신 후에만 머지.
- 모델/엔진 교체(Claude↔Codex↔Copilot): 첫 5건은 QA 이중 검토(qa+reviewer), 깊이 D 등급 분포가 기존과 다르면 finding.

## Consequences
- 누수 감지가 사람의 기억이 아니라 5분 주기 점검으로 옮겨진다. "끝났다"는 closeout 파일과 태그로만 성립한다.
- 구현: OPS-19(누수 스캐너), OPS-20(마일스톤 종결 자동화·hold_gate), OPS-21(리팩터링 게이트·경로 잠금), BE closeout_check.py, BE 코드 래칫 스크립트.
