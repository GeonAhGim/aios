# 8.2-D 종단간 지연 벤치마크

정책문서 8.2-D · 06_mvp_scope_v1.3.md#SS6.3 Definition of Done

측정 대상: InProcessEventBus.publish() -> 구독 handler 처리 시작까지(Phase 1 단일 프로세스 구조라 이게 곧 '종단간').

- 샘플 수: 200
- 평균: 0.036 ms
- p50: 0.032 ms
- p95: 0.050 ms
- 최대: 0.140 ms
- 목표(Draft): 50.0 ms
- 목표 충족 여부(p95 기준): 예

측정 자체가 Phase 1 SCAFFOLD 완료조건이며, 목표 미달이어도 조건은 충족한다(FROZEN 착수 조건인 20.1-A A그룹 통과와는 별개).
