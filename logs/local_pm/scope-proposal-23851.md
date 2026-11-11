# 파일 범위 분할 제안: task-23848

## 분석
- 원본 files: `["src/"]` — 디렉터리라 파일 범위로 해석 불가(`unresolved_path_scope`).
- 단계 출력의 `from_status=SUBMITTED != folded status so far (CREATED)` 문구는 `src/core/eventstore/projections/orders.py:64`(EventChainBrokenError)에서 나온다.
- 이 문구가 찍힌 줄은 `scripts/replay_verify.py:261`의 stderr 경고(pre-cutover 주문 skip, x59)이며 그 자체는 실패가 아니다. 실제 pytest_perf 적색 원인은 잘린 출력의 별도 단언이므로, 분할 전에 로컬에서 pytest_perf 단계를 실행해 실패 테스트를 특정해야 한다.

## 제안 분할 (순서대로)
1. 조사 leaf (코드 수정 없음): pytest_perf 단계 실행 → 실패 테스트/수치 특정. 읽기 대상: `scripts/replay_verify.py`, `tests/perf/`.
2. 정정 leaf A (원인이 replay 경고 노이즈/지연일 때): `scripts/replay_verify.py`, `tests/adversarial/eventstore/test_no_state_change_without_event.py`.
3. 정정 leaf B (원인이 projection 순서 검증일 때): `src/core/eventstore/projections/orders.py`, `src/services/oms/adapters/order_events_repository.py`.
4. 정정 leaf C (원인이 perf 측정일 때): 실패한 `tests/perf/` 파일 1개 (예산 상향 금지, 픽스처 경유).

## 비고
- 원본 hold 해제·재큐·코드 수정 없음. 기준선/예산 상향 금지(DECISION_GUIDELINES B-2).
