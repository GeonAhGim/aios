# L4 집행 계층(EMS: 라우팅·집행 알고리즘·거래비용분석) 명세 v1.0

## 0. 문서 메타
- status: Accepted (2026-09-06) — ADR-2026-09-06-B D5의 실행 명세
- owner role: Chief Architect(포트 경계 승인), PM(리프 배정)
- depends on: L4-01~29(OMS·거래소 어댑터), FA-1~8(엔티티·배분), CM-8(사전 컴플라이언스), DC-19~26(마이크로구조), R-27~37(리스크 게이트)
- implemented by: `src/foundation/ems/**`, `src/services/oms/**`(부모-자식 확장), `frontend/apps/web/src/execution/**`
- 리프 접두: **EM**
- **원칙**: 집행 전략은 포트 뒤에 있고, 어떤 알고리즘도 리스크·컴플라이언스 게이트를 우회하지 못한다. 자식 주문도 부모와 동일하게 게이트를 통과한다.

## 1. 기관급 요구
| 요구 | 내용 | 강제 지점 |
|---|---|---|
| 부모-자식 주문 | 알고리즘 주문이 자식 주문을 생성, 부모 상태는 자식 집계로 산출 | EM-2~3 |
| 벤처 선택 | 다중 벤처 상장 시 비용·유동성·수수료 기준 라우팅, 선택 근거 기록 | EM-4~6 |
| 집행 알고리즘 | TWAP·VWAP·POV·IS(Implementation Shortfall) 최소 4종, 결정론 스케줄 | EM-7~11 |
| 거래비용분석 | 도착가·VWAP·종가 대비 슬리피지, 수수료·마켓임팩트 분해 | EM-12~14 |
| 게이트 불가침 | 자식 주문도 리스크·컴플라이언스 통과, 알고리즘은 한도를 늘릴 수 없다 | EM-3, EM-16 |
| 회선 확장 | FIX 4.4/5.0은 포트 뒤 어댑터(MVP-2) | EM-17 |

## 2. 모듈 분해 (파일 ≤300줄)
| 파일 | 책임 |
|---|---|
| `contracts/v1.py` | `ParentOrder{parent_id, instrument_id, side, qty, algo: AlgoSpec, constraints, fund_id, portfolio_id}`, `ChildOrder{child_id, parent_id, slice_seq, ...}`, `RouteDecision{venue, reason_codes, expected_cost_bps}`, `TcaResult{arrival_bps, vwap_bps, impact_bps, fees_bps, opportunity_bps}` |
| `domain/parent_child.py` | 부모 상태 집계 규칙(자식 체결 합 → 부모 상태), 취소·정정 전파(순수) |
| `domain/route/{venue_scoring,fee_model,liquidity_model}.py` | 벤처 점수화(수수료·스프레드·깊이·최근 체결률), 결정론 |
| `domain/algo/{twap,vwap,pov,is_shortfall}.py` | 슬라이스 스케줄 생성(순수 함수: 시장 상태 스냅샷 → 자식 주문 계획) |
| `domain/algo/guard.py` | 알고리즘 제약(최대 참여율·슬라이스 간격·잔여 마감 처리) |
| `domain/tca/{benchmarks,decomposition}.py` | 도착가·구간 VWAP·종가 벤치마크, 비용 분해(순수) |
| `application/{start_algo,tick_algo,cancel_algo}.py` | 알고리즘 수명주기(스케줄러 tick에서 호출, 자식 주문 제출은 기존 `submit_order` 경유) |
| `application/route_order.py` | 라우팅 결정 + 근거 저장 |
| `application/compute_tca.py` | 체결 종료 후 TCA 산출·저장 |
| `ports/fix_session.py` | FIX 세션 포트(어댑터 MVP-2) |
| `adapters/postgres_*.py` + 마이그레이션 | `parent_orders`·`child_orders`·`route_decisions`·`tca_results` |
| 프론트 `ExecutionAlgoPage.tsx`, `TcaPage.tsx` | 알고리즘 주문 생성·진행률, TCA 리포트 |

## 3. 계약 (요지)
- `AlgoSpec{kind: twap\|vwap\|pov\|is\|iceberg, start, end, max_participation_pct, slice_interval_sec, urgency, limit_price\|None, seed}`.
  **OMS §3.1 `AlgoRequest`를 대체한다**(감사 2026-09-06): enum은 여기 정의가 정본이고 ICEBERG를 흡수한다. `AlgoRequest`의 `size_jitter_pct`/`time_jitter_pct`는 EM-A3(결정론)와 충돌하므로 **`seed`를 계약에 포함한 의사난수로만** 허용한다(같은 seed → 같은 계획).
- `SubmitOrderCommand`의 기존 `parent_order_id`/`algo_run_id`를 그대로 쓴다 — `parent_orders`/`child_orders`는 그 필드의 저장소이지 별도 식별자 체계가 아니다.
- **자식 주문은 예외 없이 `submit_order`를 통과한다** — 리스크·컴플라이언스 판정 ID를 각각 갖는다. 알고리즘이 직접 어댑터를 부르는 경로는 없다.
- 라우팅 결정은 `reason_codes`(예: `BEST_FEE`, `DEEPEST_BOOK`, `ONLY_VENUE`)와 점수 스냅샷을 저장해 최선집행 보고(CM-14)의 입력이 된다.
- TCA 벤치마크 기준시각은 부모 주문 `arrival_ts`(도착)와 자식 체결 구간으로 고정.
- 에러: `EM_ALGO_CONSTRAINT`(400), `EM_NO_ROUTE`(409), `EM_PARENT_TERMINAL`(409), `EM_PARTICIPATION_EXCEEDED`(409).

## 4. 불변조건
- **EM-A1** 자식 주문 수량 합 ≤ 부모 수량. 초과 슬라이스 생성은 도메인에서 거부.
- **EM-A2** 알고리즘은 리스크·컴플라이언스 한도를 완화할 수 없다(자식이 부모보다 넓은 권한을 갖지 못함, 적대적 테스트).
- **EM-A3** 슬라이스 스케줄은 결정론이다(같은 스냅샷·같은 스펙 → 같은 계획). 난수 사용 시 시드가 계약에 포함.
- **EM-A4** 부모가 터미널이면 신규 자식 생성 금지, 미체결 자식은 취소 요청.
- **EM-A5** TCA는 저장된 체결·시세 계보만 사용한다(사후 데이터 정정 시 재계산 이력을 남긴다).

## 5. 동시성·멱등성
- 슬라이스 생성: `(parent_id, slice_seq)` 유일. 알고리즘 tick: 부모별 advisory lock. TCA: `(parent_id, revision)` 유일.

## 6. 실패 모드
| 실패 | 조치 |
|---|---|
| 벤처 장애 | 라우팅 재평가, 대체 벤처 없으면 `EM_NO_ROUTE` + 알고리즘 일시정지 |
| 참여율 초과 위험 | 슬라이스 축소 또는 스킵, 사유 기록 |
| 자식 UNKNOWN | 부모 진행 중지, 역조회 resolver 완료까지 대기(중복 발주 금지) |
| 마감 임박 잔여 | `urgency` 정책에 따라 잔여 일괄 또는 미체결 종료(사유 기록) |

## 7. SLO
- 라우팅 결정 p99 20ms, 슬라이스 계획 생성 p99 50ms, TCA 산출 ≤ 30초/부모 주문.

## 8. 테스트
- 적대적: 자식이 게이트 우회 시도, 부모 초과 슬라이스, 참여율 조작, 터미널 부모에 자식 생성, TCA 입력 변조.
- 계약: 알고리즘 4종 스케줄 스냅샷(결정론), 라우팅 점수 경계, TCA 분해 합 == 총비용.

## 9. 리프 목록
| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| EM-1 | `contracts/v1.py` + 스냅샷 | FA-1 | 스키마 | 300 |
| EM-2 | `domain/parent_child.py` + test — **감사 2026-09-06: 이미 구현된 부분이 있다.** `orders.parent_order_id`·`algo_run_id` 컬럼과 `src/services/oms/domain/algo_slicer.py`(TWAP/VWAP/POV/iceberg 슬라이스 계획)가 존재하므로 **신설이 아니라 집계·전파 규칙 보강**이다 | EM-1 | 집계·전파 규칙, 초과 거부 | 200 |
| EM-3 | **기존 `orders.parent_order_id`/`algo_run_id`를 정본으로 쓰고 별도 `parent_orders`/`child_orders` 테이블은 만들지 않는다**(감사 2026-09-06). 부족한 집계 컬럼만 추가 + **자식 주문이 submit_order 경유임을 정적 증명** | EM-2, L4-09, CM-8 | EM-A2 적대적 통과 | 560 |
| EM-4 | `domain/route/fee_model.py` + `liquidity_model.py` + test | EM-1 | 수수료 티어·깊이 점수 | 400 |
| EM-5 | `domain/route/venue_scoring.py` + test | EM-4 | 결정론 점수·타이브레이크 | 260 |
| EM-6 | `application/route_order.py` + `route_decisions` 저장 + 통합 | EM-5 | 근거 저장, 단일 벤처 폴백 | 300 |
| EM-7 | `domain/algo/guard.py` + test | EM-1 | 참여율·간격·잔여 규칙 | 240 |
| EM-8 | `domain/algo/twap.py` — 기존 `oms/domain/algo_slicer.py`를 EMS 포트로 승격(재작성 금지) + test | EM-7 | 스케줄 스냅샷·결정론 | 240 |
| EM-9 | `domain/algo/vwap.py` + test | EM-7, DC-22 | 거래량 프로파일 기반 | 280 |
| EM-10 | `domain/algo/pov.py` + test | EM-7 | 실시간 참여율 추종 | 260 |
| EM-11 | `domain/algo/is_shortfall.py` + test | EM-7 | 긴급도-비용 트레이드오프 | 280 |
| EM-12 | `domain/tca/benchmarks.py` + test | EM-1 | 도착가·VWAP·종가 | 260 |
| EM-13 | `domain/tca/decomposition.py` + test | EM-12 | 분해 합 == 총비용 | 260 |
| EM-14 | `application/compute_tca.py` + 저장 + 통합 | EM-13, EM-3 | 재계산 이력 | 300 |
| EM-15 | `application/{start_algo,tick_algo,cancel_algo}.py` + 스케줄러 배선 + 통합 | EM-8~11, EM-6 | 4종 알고 실행, 취소 전파 | 500 |
| EM-16 | 적대적 스위트 `tests/adversarial/ems/*`(게이트 우회·초과·터미널) | EM-15 | 전 케이스 차단 | 300 |
| EM-17 | `ports/fix_session.py` + 계약 테스트(어댑터 없이 포트만) | EM-1 | 포트 계약 확정 | 200 |
| EM-18 | 프론트 `ExecutionAlgoPage.tsx` + `TcaPage.tsx` | EM-15, EM-14 | 진행률·리포트 화면 | 560 |

## 10. 미확정·리스크
- FIX 회선·다크풀·조건부 주문은 MVP-2. 포트만 확정한다.
- VWAP 거래량 프로파일은 DC-22(틱→캔들) 데이터 품질에 의존한다. 데이터가 얕은 벤처는 TWAP로 자동 강등(사유 기록).
- PAPER 단계에서는 알고리즘 효과가 시뮬레이터 체결 모델(L4-22/23)에 의존하므로, TCA 수치는 실거래 개통 전까지 상대 비교용이다.

## 11. 진행 현황 (자동 생성)

<!-- spec-status:begin (auto-generated by C:/aios/pm/spec_status.py — do not edit) -->
갱신 2026-09-10T17:02:14+00:00 · 총 18 · done 14 · inflight 4 · untouched 0 · hold 0 · 재오픈 14 · 깊이(done) {'D2': 3, 'D3': 2, 'D?': 9}

| ID | 리프 | 상태 | done task | 열린 task | depth | commit |
|---|---|---|---|---|---|---|
| EM-1 | contracts/v1.py + 스냅샷 | done | 2526,2533,3125 | 3115,3116,3117 | D2 | 957ae247 |
| EM-2 | domain/parent_child.py + test — 감사 2026-09-06: 이미 구현된 부분이 있다. orders.p | done | 2070,2121 | 3114,3126 | D3 | f553385a |
| EM-3 | 기존 orders.parent_order_id/algo_run_id를 정본으로 쓰고 별도 parent_orders/child_ | done | 2121 | 3126,3542 | D3 | f553385a |
| EM-4 | domain/route/fee_model.py + liquidity_model.py + test | done | 2106,2119,2120 | 3115,3116,3117 |  | 3c5c65fe |
| EM-5 | domain/route/venue_scoring.py + test | done | 2106,2119,2120 | 3115,3116,3117 |  | 3c5c65fe |
| EM-6 | application/route_order.py + route_decisions 저장 + 통합 | done | 2120,2526 | 3117 | D2 | f18d1a20 |
| EM-7 | domain/algo/guard.py + test | done | 2457,2499 | 3118 |  | 0688738d |
| EM-8 | domain/algo/twap.py — 기존 oms/domain/algo_slicer.py를 EMS 포트로 승격(재작성 금지) | done | 2461 | 3119 |  | 1968707d |
| EM-9 | domain/algo/vwap.py + test | done | 2501 | 3124 |  | c43e2c82 |
| EM-10 | domain/algo/pov.py + test | done | 2499 | 3120 |  | 0688738d |
| EM-11 | domain/algo/is_shortfall.py + test | done | 2502 | 3122 |  | 5831b823 |
| EM-12 | domain/tca/benchmarks.py + test | done | 2500,2526,2867 | 3121 | D2 | cd1a00de |
| EM-13 | domain/tca/decomposition.py + test | done | 2511 | 3123 |  | bac4e446 |
| EM-14 | application/compute_tca.py + 저장 + 통합 | inflight |  | 2669,2672 |  |  |
| EM-15 | application/{start_algo,tick_algo,cancel_algo}.py + 스케줄러 배선 + 통합 | inflight |  | 2670,2671 |  |  |
| EM-16 | 적대적 스위트 tests/adversarial/ems/(게이트 우회·초과·터미널) | inflight |  | 2671 |  |  |
| EM-17 | ports/fix_session.py + 계약 테스트(어댑터 없이 포트만) | done | 2533,3125 | 2746 |  | 957ae247 |
| EM-18 | 프론트 ExecutionAlgoPage.tsx + TcaPage.tsx | inflight |  | 2672 |  |  |
<!-- spec-status:end -->
