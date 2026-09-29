# ADR-2026-09-30-A: 로컬 PM 대리 임무 — 대시보드 호출·로컬 워커풀 자가복구

## Status
Accepted (2026-09-30, Chief Architect). 사용자 지시 2026-09-30 07:20: "pm 역할을 로컬 워커풀에서 가능한 영역만 대리 수행, 관제대시보드에서 내가 로컬 PM을 호출해 Claude Code 없이도 작업을 이어가게, 로컬 워커풀 에러 시 로컬 PM이 조치하고 다시 정상 가동".

## Context
- PM 사이클은 Claude(sonnet/haiku) 헤드리스 세션이 돈다. 세션 한도·Claude Code 부재 시 함대는 "리프가 있는 동안만" 돌고, 새 후속 발행·결정·복구는 멈춘다.
- 이미 있는 조각: `pm_cycle.py --local`(task-3913, 로컬 모델로 후속 implement 리프 발행만), local-triage 레인(읽기 전용 1차 분류), 대시보드 '점검/안전복구' 버튼(task-4888, localhost POST), ND 규칙 30여 개(auto_decision), 오늘 발행한 자기수리 4종(8990~8993).
- 로컬 모델(27B급)은 규칙이 명확한 기계적 판단에는 충분하고, 명세 작성·아키텍처·S-tier 판단에는 부족하다(ADR-2026-09-26-C 실측: 사람 수준 리뷰만 잡은 결함 3건).

## Decision
1. **로컬 PM 레인 `local-pm`** (engine claude-local, pools.yaml size 1, 로컬 슬롯 예약 1): `pm_cycle.py --local`을 확장한 전용 워커. **허용 임무(화이트리스트)**: (a) ready가 바닥난 role의 후속 리프 발행(기존), (b) needs_decision 중 ND 규칙표에 이미 있는 기계적 결정만 decision 기록(근거 필수, 규칙 id 명시), (c) 대체·중복 QA supersede(ND-2s 결과 기반), (d) 로컬 레인 hold 해제·재배정·재큐, (e) 로컬 LLM·프록시·레인 복구(`dashboard_local.local_llm_recover` 등 기존 스크립트 호출), (f) 에스컬레이션 triage 제안(기존 local-triage 흡수). **금지**: docs/specs·ADR 수정, esc-health 종결, S-tier·마이그레이션 리프 판단, git 쓰기, pools.yaml·게이트·기준선 수정, 자유 셸. 모든 행동은 **액션 러너**(JSON 액션 목록 → 화이트리스트 스크립트)로만 실행되고 `state/local_pm_actions.jsonl`에 남는다.
2. **대시보드 호출**: '로컬 PM' 카드 — 지시문 입력 + 실행 버튼 + 최근 실행 결과/액션 로그. `POST /api/local-pm/run`은 localhost 또는 테일넷(100.64.0.0/10)에서 토큰(state 파일, 대시보드가 헤더로 전달)과 함께만 허용. 호출은 `role=local-pm` task(P0, spec=지시문)를 create_task로 만들고 orchestrator가 스폰한다 — 대시보드가 모델을 직접 부르지 않는다. 지시문이 금지 영역이면 task note에 "범위 밖 — CTO 필요"로 종료.
3. **자동 트리거(자가복구)**: healthcheck가 로컬 풀 장애 소견(llama/proxy down, 로컬 레인 lane_idle, 워커 크래시 루프, budget_miss 폭증, RAM 하한 지속)을 내면 쿨다운(30분) 안에서 `local-pm` 복구 task를 자동 발행. 복구 후 레인 가동을 확인(스폰 재개 실측)하고, 2회 실패 시 esc-health(owner=사람)로 승격. 복구 task는 다른 로컬 task보다 먼저 스폰된다(예약 슬롯).
4. **불변식**: 로컬 PM은 Claude PM의 대체가 아니라 부분집합이다 — Claude PM이 돌아오면 같은 규칙표를 쓰므로 충돌 없음. 화이트리스트 밖 행동은 코드 수준에서 불가(프롬프트 금지가 아니라 러너가 거부). 사용자 지시문도 화이트리스트 안에서만 실행된다.

## Consequences
- 리프: 4건(ops) — ① local-pm 레인·프롬프트·액션 러너 ② 대시보드 카드·API·토큰 ③ healthcheck 자동 트리거·검증·승격 ④ 감사 로그·대시보드 표시·테스트.
- 되돌림: pools.yaml local-pm size 0 + 대시보드 카드 숨김.
