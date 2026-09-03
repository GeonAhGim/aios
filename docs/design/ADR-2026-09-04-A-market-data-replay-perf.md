# ADR-2026-09-04-A: 시장데이터 리플레이 성능 계약과 lineage 해시의 증분화

## Status
Accepted (2026-09-04, Chief Architect). 에스컬레이션 esc-826-perf-contract(LA-23) 판단.

## Context
연단위 M1 리플레이(525,600 캔들) 실측 55.9s. 분해: DB fetch 11.1s, CandleRecord pydantic 검증 16.1s,
batch_hash(레코드별 canonical JSON + 전체 정렬 + sha256) 24.7s, 세션/gap 4.7s. §8.4의 "5s" 목표는
측정 규모가 명시되지 않았고, DB 왕복 1회만으로도 5s를 넘는다.

## Decision
1. **contracts/v1 CandleRecord는 바꾸지 않는다(P5).** API·프론트(parseCandleSeries)는 그대로.
   대신 **내부 읽기 전용 컬럼지향 모델 `CandleColumns`**(ts/o/h/l/c/v 배열)와 포트
   `read_candles_columnar()`를 추가한다. 쓰기 시점에 이미 검증된 데이터이므로 이 경로는 레코드별
   pydantic 검증을 하지 않는다(무결성은 DB 제약과 lineage 해시가 담보). 리플레이·백테스트·품질검사
   같은 대량 소비자만 이 경로를 쓴다.
2. **batch_hash는 결과값이 동일한 스트리밍 구현으로 바꾼다.** 정렬 키 순서로 `ORDER BY` 해서
   가져오면 전체 정렬이 사라지고, 직렬화는 canonical JSON 규칙을 유지한 채 증분 sha256으로 흘린다.
   **기존 저장 해시와 바이트 단위로 동일해야 하며(동일성 테스트 필수), 마이그레이션·backfill은 없다.**
   동일성을 깨는 최적화가 꼭 필요하면 `hash_version=2`를 새로 두고 v1 검증 경로를 유지한다 — 저장된
   해시를 다시 쓰는 일은 금지(P3 WORM).
3. **성능 계약을 규모별로 명시한다(단일 노드, P95):** 1일(1,440) ≤ 0.5s, 1개월(43,200) ≤ 5s,
   1년(525,600) ≤ 30s. §8.4의 "5s"는 1개월 기준으로 해석·수정한다. 연단위는 30s 계약으로
   측정하되 CI에서는 1개월 규모까지만 강제하고 연단위는 nightly 성능 잡으로 돌린다.

## Consequences
- LA-23은 PM 결정대로 종결(1일 5s 통과 + 연단위 xfail). 후속 리프 LA-23b가 1·2·3을 구현한다.
- 컬럼지향 경로는 계약 파일이 아니라 `domain/`·`ports/` 내부 모델이므로 Guard P5 대상이 아니다.
- 리스크·백테스트(R/BT 리프)는 처음부터 `read_candles_columnar()`를 소비한다.

## Rejected
- contracts/v1를 벌크 반환으로 바꾸는 안: 모든 소비자 계약 변경, P5 위반.
- 저장 해시 재계산·backfill: WORM 위반이며 감사 체인의 신뢰를 깬다.
