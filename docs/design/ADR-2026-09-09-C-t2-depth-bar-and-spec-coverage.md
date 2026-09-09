# ADR-2026-09-09-C: T2 깊이 기준(D2)과 명세 커버리지 기계 검증

## Status
Accepted (2026-09-09, Chief Architect). 사용자 지시 "구현됐다 해도 기초 수준이면 의미가 없다. 최소 T2급 이상 성능".

## Context
- 명세 리프 416건 중 task가 없는 행 123건(AI-1~22, UX-1~22, RD-6~18, FA-17~24, MP/SIG, CM-12/18/19, EM-14~16/18, PLT-35/40/42, R-47,
  최근 워커가 추가한 BT-15/16·DSL-16·CH-13/17~19·DC-26·IND-11 등). PM 사이클이 "행이 있는데 task가 없다"를 사람 없이 잡지 못했다.
- task 2549가 PM의 직접 파일 쓰기로 덮여(IND-11 → OPS-12) 리프 하나가 조용히 사라졌다.
- "done"의 깊이가 균일하지 않다. 같은 done이라도 happy path 하나만 있는 리프와 적대·복구·성능까지 증명된 리프가 섞여 있다.
- 등급표(ADR-2026-09-04-D): T1 기관 OMS/EMS(Aladdin·Bloomberg AIM), T2 기관급 통제 + 프로 퀀트 엔진, T3 NautilusTrader·LEAN·Freqtrade급.

## Decision 1 — 리프 깊이 등급 D0~D3, 완료 하한은 D2
| 등급 | 뜻 | 증빙 |
|---|---|---|
| D0 | 스텁·placeholder·NotImplemented·TODO | — |
| D1 | happy path 동작 + 단위 테스트 | 테스트 통과 |
| **D2 (T2 하한)** | 경계·실패·동시성·복구 경로가 테스트로 증명되고, 성능 예산이 수치로 걸려 있으며, CI 게이트가 실제로 막는다 | negative 테스트 ≥3, 실패 주입 1, 성능 단언 1(p95/p99 또는 처리량), 게이트 적색 재현 1 |
| D3 | 적대 테스트·리플레이 재현·장기 소크·다중 인스턴스까지 증명 | RED_TEAM/INVARIANTS 대조, replay_verify 통과 |

- 앞으로 모든 implement 리프는 **D2 미만이면 QA가 done을 찍지 못한다**. QA는 task note에 `depth=D<n>`과 근거(테스트 이름)를 적는다.
- 안전·실행·원장·컴플라이언스·데이터 축(R, L4, LA/LB/LC, FA, CM, EO, DC)은 **D3**가 하한이다(INVARIANTS I-10 "배선·우회불가·증명됨").
- 축별 성능 예산(로컬 기준, CI 단언): 사전거래 게이트 p99 5ms, 주문 제출→ACK p95 50ms(paper), 5k봉 조회 p95 200ms, 백테스트 1개월 M1 1심볼 3초,
  DSL 컴파일 300ms, 지표 증분=일괄 동일, 스크리너 5k 심볼 2초, WS 팬아웃 p95 500ms, 리스크 리플레이 24h 60초.

## Decision 2 — 기존 done 리프 소급 깊이 감사(DEEPEN)
- 축별 depth 감사 task(QA 역할)를 발행한다. 각 감사는 done 리프를 전수(축당 ≤60건) 대조해 D 등급을 매기고, D2 미만이면
  `DEEPEN <ID>` implement task를 create_task로 발행한다(우선 2, 안전축은 우선 1). 감사 결과는 `docs/audit/DEPTH_<축>.md`에 표로 남긴다.
- DEEPEN task의 DoD는 D2(안전축 D3) 증빙 자체다. 새 기능 추가 금지, 깊이만 올린다.

## Decision 3 — 명세 커버리지 기계 검증
- pm healthcheck에 `spec_coverage` 점검 추가: docs/specs/L4_*.md 표의 리프 ID 중 implement task가 없는 ID를 찾아 medium finding으로 올리고,
  PM 사이클은 이 finding을 보면 해당 행에서 task를 발행한다(사람 개입 없음). MP/SIG처럼 ADR로 보류된 축은 `spec_hold.yaml`에 사유와 함께 등록해 제외한다.
- task 생성은 반드시 `orchestrator.create_task`(O_EXCL)로 한다. PM·워커의 직접 `tasks/task-<id>.json` 쓰기는 금지하며, healthcheck가
  "id 재사용(같은 id의 제목이 바뀜)"을 탐지하면 high finding으로 올린다(task_id_overwrite).

## Decision 4 — 이번에 발행하는 것
- 미발행 리프 전부를 create_task로 발행한다(MP-1~10·SIG-1~6은 M2-HOLD에 묶어 보류). 우선순위: 안전/실행 보강(R-47, L4, CM, EM, PLT) 2,
  사용자 가치(UX, AI, RD) 3, FA-17~24 4.
- IND-11을 재발행한다(2549 덮임).
- QA 지침(WORKER_PROMPT_qa.md)에 D 등급 판정과 done 하한을 넣는다.

## Consequences
- 완료 수치는 잠시 내려간다(DEEPEN 재오픈). 그 대신 "done = T2 이상"이 기계적으로 보장된다.
- QA 부하가 늘어 qa 풀을 자동 재조정(OPS-12)에 맡긴다.
