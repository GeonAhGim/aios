# ADR-2026-10-01-A: 사용자 관점 1등급 지표 축 — 체감 성능·기능 범위·편리함·전략 성과 (MVP-2 종결 조건 보강)

## Status
Accepted (2026-10-01, CTO 검토). 원 작성: backend-3 worker, task-10599. D1 구현(체감 성능 계측
헬퍼·J1~J3 여정 측정·기준선 수집)은 task-10800으로 반영했다 — 본 Decision 본문은 수정하지 않았다.

## Context (실사 2026-10-01)
- 사용자 정의(2026-10-01): "엔터프라이즈 프런티어 1등급"은 사용자 관점에서 좋은 체감 성능, 다양한
  기능, 높은 편리함, 높은 수익률을 내는 서비스다. 지금까지의 종결 지표는 전부 **공급자(엔지니어링)
  관점** 지표다 — [[ADR-2026-09-26-C]](ADR-2026-09-26-C-stabilization-phase-exit-by-metrics.md)의
  MVP-1 종결 지표(CI 연속 녹색·재오픈 0·자기조치 비율·J1~J3 e2e·D? 0)와
  [[ADR-2026-09-09-C]](ADR-2026-09-09-C-t2-depth-bar-and-spec-coverage.md)의 깊이 등급(D0~D3)은
  전부 "구현이 신뢰할 수 있게 동작하는가"를 측정하며, "사용자가 실제로 그것을 쓰고 싶어하는가"는
  측정하지 않는다.
- 이 ADR은 **신뢰성 하한(위 두 ADR)을 바꾸거나 완화하지 않는다** — 그 위에 사용자 관점 축을 추가한다.
  두 ADR 본문은 참조만 하고 수정하지 않았다(사용자 지시 "금지" 항목 준수).
- 작성 범위: 문서만. 코드·테스트·게이트는 변경하지 않았다.

## Decision

### D1. 체감 성능(Perceived Performance)

**측정 방법·데이터 출처**
- 서버측 API 지연: 기존 observability 축(`src/core/observability`, `src/foundation/*` 각 어댑터의
  `prometheus`/`httpx` 계측)이 p95/p99를 수집한다. 축별 성능 예산은 이미
  [[ADR-2026-09-09-C]](ADR-2026-09-09-C-t2-depth-bar-and-spec-coverage.md) Decision 1에 고정돼
  있다(사전거래 게이트 p99 5ms, 주문 제출→ACK p95 50ms, 5k봉 조회 p95 200ms, DSL 컴파일 300ms 등).
  이 ADR은 새 예산을 만들지 않고 기존 예산이 **화면 단위 사용자 체감**과 연결돼 있는지만 점검한다.
- 프런트엔드 체감 지연(화면 최초 표시·상호작용 가능 시점, Core Web Vitals류)은 **저장소 실사 결과
  측정 코드가 없다** — `frontend/apps/web/src` 전체에서 `web-vitals`/`PerformanceObserver`/
  `reportWebVitals` 패턴을 검색했으나 0건(이 리프의 grep 결과). `docs/specs/UX_JOURNEYS.md` §4는
  "스크리너 결과 표시 p95 ≤2s", "차트 5k봉 렌더 p95 ≤200ms", "첫 화면 상호작용 가능 시간 p95
  ≤2.5s"를 목표로만 적어 두었을 뿐, 실측 계측기는 아직 없다.
- 목표값 후보(저장소에 이미 적힌 값 재사용, 새로 만들지 않음): `docs/specs/UX_JOURNEYS.md` §4의
  3개 수치, [[ADR-2026-09-04-B]](ADR-2026-09-04-B-tradingview-parity-and-global-data-coverage.md)
  D1의 "5k봉 조회 p95 200ms" 설계 기준.
- 기계 판정 방법(제안, 미구현): 프런트엔드에 `web-vitals` 라이브러리(또는 동등 네이티브
  `PerformanceObserver` 래퍼)를 붙여 LCP·INP를 수집하고, `frontend/e2e/journey-j*.spec.ts`
  실행 중 Playwright의 `page.evaluate(() => performance timing)` 또는 트레이싱 API로 화면 전환
  지연을 함께 기록하는 CI 단계를 추가하는 안. **이 ADR은 설계 방향만 제시하고 구현하지 않는다**
  — 구현은 별도 FE/PLT 리프로 발행해야 한다.

**미확인**
- 실사용자 환경(느린 네트워크·저사양 기기)에서의 체감 지연은 코드만으로 판정 불가.
- API p95/p99가 실제로 어느 대시보드에 상시 노출되는지(알림·회귀 감지 배선 여부)는 이번 실사
  범위 밖 — `src/core/observability`의 존재만 확인했고 대시보드 연동은 미확인.

### D2. 기능 범위(Feature Parity) — TradingView 동등 프로그램 대조표

근거: [[ADR-2026-09-04-B]](ADR-2026-09-04-B-tradingview-parity-and-global-data-coverage.md)가 정의한
7개 하위영역(SIG/DC/IND/DSL/BT/CH/MP)과 `docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md`
§11 진행 현황(2026-09-24 자동 생성, 이 리프에서 재확인)을 그대로 집계했다(추측 없음).

| 영역 | TradingView 대응 기능 | 명세 리프 수 | 보유(done) | 보류(hold) | 비고 |
|---|---|---|---|---|---|
| CH (차트·드로잉·레이아웃) | 차트 렌더링·지표 오버레이·레이아웃 | 20 | 20 | 0 | §11 집계. 자체 렌더링 엔진([[ADR-2026-09-04-B]] D2) 완주 |
| IND (지표) | 기술지표 라이브러리 | 9(이 L4 파일 기준) | 9 | 0 | TA-Lib 전종 + OSS 확장은 [[ADR-2026-09-26-E]](ADR-2026-09-26-E-indicator-platform-wasm-oss-verification.md)로 별도 확장 중 — 이 표는 analytics_authoring 명세 범위만 집계, 전체 IND 리프 수는 별도 확인 필요(미확인) |
| DSL (전략 스크립트 언어) | Pine Script | 16 | 16 | 0 | "AIOS Script"(결정론 샌드박스), Pine 호환 실행기는 Rejected([[ADR-2026-09-04-B]] Rejected 절) |
| BT (백테스트) | 즉시 백테스트·Deep Backtesting | 18 | 18 | 0 | 즉시+딥 동일 엔진 |
| DC (데이터 커버리지) | 글로벌 벤더 데이터 | 26 | 25(+1 inflight) | 0 | 벤더 계약 자체는 사람 결정 사항으로 명세 밖(§10 미확정) |
| MP (마켓플레이스) | 공개 스크립트 마켓 | 10 | 0 | 10 | **M2-HOLD** — [[ADR-2026-09-09-C]](ADR-2026-09-09-C-t2-depth-bar-and-spec-coverage.md) Decision 4가 보류 등록. 현재 "없음" |
| SIG (웹훅 신호 유입) | TradingView 알림 웹훅 | 6 | 0 | 6 | 선택 백로그, 의도적 최후순위([[ADR-2026-09-04-B]] D6) |

**해석**: CH·IND·DSL·BT·DC는 명세 리프 기준 완주(done)이지만, "done"은 [[ADR-2026-09-09-C]] D2/D3
깊이 하한을 의미할 뿐 TradingView와의 **기능 폭** 동등성을 보장하지 않는다 — 예: `docs/specs/
L4_analytics_authoring_backtest_marketplace_v1.0.md` §10은 IND 100종 목록·포크 차트 엔진 라이선스
확정·pyarrow 채택 여부 등을 "미확정"으로 남겨 두었다. MP(마켓플레이스)는 TradingView 대비 가장
큰 기능 공백이다 — 10개 리프 전부 보류 상태.

**미확인**
- IND 전체(이 L4 파일 밖, 별도 지표 플랫폼 확장분 포함) 리프 수와 TA-Lib 대비 커버리지 퍼센트.
- 벤더 데이터 커버리지(실제 연결된 거래소/브로커 수 vs TradingView의 전 세계 커버리지) 정량 비교.

### D3. 편리함(Convenience) — UX_JOURNEYS J1~J3 현황 + 추가 여정 후보

근거: `docs/specs/UX_JOURNEYS.md`(이 task의 files에 포함, 선행 리프 task-10612가 이미 §6을
"ADR-2026-10-01-A '편리함' 축 보강" 입력 자료로 작성해 둔 상태를 그대로 인용한다).

- **J1(온보딩)·J2(발견·백테스트)·J3(페이퍼 주문)**: 코드·e2e 모두 그린(`UX_JOURNEYS.md` §1 J1~J3
  표, 2026-10-01 재조사 기록 — J1 closeout 적색은 CI 오케스트레이션 거짓양성으로 판명, §1 "J1
  closeout 적색 재조사" 절). 단 §1 "J1 사용감 소견"에 기록된 F-1~F-6 마찰 지점 중 F-1~F-6 전부
  task-10642로 해소됨(`UX_JOURNEYS.md` §1 표 내 "해소(task-10642)" 주석).
- **사용자 핵심 과업 커버리지 재확인**(`UX_JOURNEYS.md` §6.1 표): "가입→거래소 연결→전략 선택→
  모의 운용→실운용, 손실 시 중단"의 6단계 중 가입~페이퍼 주문(J1~J3)은 덮이지만, (a) **전략→모의
  운용 전환**(J7 후보, G-3·G-11과 동일 패턴 — CTA 부재로 사실상 단절), (b) **모의→실운용 전환**
  (J8 후보, 확인 단계 0건 — `ExecutionControlPage.tsx:99,178-180`의 단순 드롭다운), (c) **손실 시
  즉시 중단**(J9 후보, task-10636으로 이미 해소 — 비관리자용 대시보드 "내 운용 전부 정지" 패널)은
  §6.2에서 새 여정 J7~J9로 분리 추적된다.
- **우선순위 제안**(`UX_JOURNEYS.md` §6.5, 이 ADR이 확정 권한을 CTO에 위임): 1) J8 확인 단계
  추가(실자금 전환 리스크 최대) 2) (해소됨) J9 전역 중단 3) J7 CTA 연결 4) G-2 대시보드 알림
  위젯 5) 기타.
- 사용감 판정 방법(기계/사람 구분)은 `UX_JOURNEYS.md` §6.3 표에 여정별로 이미 정의돼 있다(예:
  J1은 "단계 수 ≤8"을 Playwright로 기계 판정, J8은 "확인 단계 ≥1회 존재"를 DOM으로 기계 판정하되
  고지 문구의 명확성은 사람 판정).

**미확인**(`UX_JOURNEYS.md` §6.4 그대로 인용)
- `AccountDeletionPage.tsx`의 2단계 확인 모달 존재 여부.
- `is_platform_admin` 플래그의 실제 운영 배포 범위(개인 트레이더 페르소나에게 부여되는지).
- J1~J3 외 화면(`/system/paper-deployments`, `/executions` 등)의 체감 지연 실측치.
- 모바일/반응형 렌더 실제 동작(G-10, breakpoint 판정은 코드만으로 불가).

### D4. 전략 성과 서비스(Strategy Performance) — 측정·공시 대상이지 보장 대상이 아니다

- [[ADR-2026-09-29-A]](ADR-2026-09-29-A-mvp2-builtin-strategy-intelligence-engine.md) 내장 전략
  지능 엔진은 "분석·생성·고도화·자가학습" 4대 책임을 가지며, 완성 기준(Decision 2)에 "페이퍼에서
  실측 지표(샤프·MDD·회전율·슬리피지 반영)로 승격 판정"이 이미 명시돼 있다 — 이 ADR은 그 기준을
  재확인할 뿐 새로 만들지 않는다.
- 성과 지표 계산 코드는 이미 저장소에 존재한다: `src/foundation/performance/domain/risk_metrics.py`
  (샤프 등 리스크 지표), `src/foundation/performance/application/compute_statement.py`(성과 명세서
  계산). 프런트 노출 지점은 `UX_JOURNEYS.md` §1 J5(`/portfolio`, `/reports`,
  `/portfolio/performance-statements`).
- 표본 외 검증(워크포워드 OOS, 과최적화 게이트)은 [[ADR-2026-09-29-A]] Decision 5 불변식("과최적화
  게이트 — 워크포워드 OOS 없이는 승격 불가")으로 이미 고정돼 있다. 이 ADR이 추가하는 것은 단
  하나: **수익률·샤프·MDD는 사용자에게 "보장값"이 아니라 "측정·공시값"으로 노출해야 한다**는 UX
  원칙이다 — 현재 `/portfolio/performance-statements` 화면 문구가 이 구분을 명시하는지는 이번
  실사에서 렌더된 문자열까지는 확인하지 못했다(미확인, 아래 참고).
- 기계 판정 방법(제안): 성과 화면에 "과거 성과이며 보장이 아님" 고지 문자열 존재 여부를 e2e에서
  텍스트 매칭으로 검사하는 negative-style 체크. **이 ADR은 설계만 제시, 구현은 별도 리프.**

**미확인**
- `/portfolio/performance-statements` 렌더 결과에 "보장 아님" 고지 문구가 실제로 있는지.
- [[ADR-2026-09-29-A]] 내장 엔진은 Status가 아직 **Proposed**이고 착수 조건이 "ADR-2026-09-26-C
  종결 지표 충족 후"이므로, 전략 성과 서비스 자체가 아직 MVP-2 범위에서 가동 전이다 — 실측 수치는
  현재 없음(설계만 존재).

### D5. 깊이 심화 순서 제안 — 사용자 가치가 큰 축부터

현재 하한은 [[ADR-2026-09-09-C]] D2(안전축 D3)다. 이 ADR은 하한을 바꾸지 않고, **D2를 넘어 D3로
올리는 순서**를 사용자 가치 기준으로 제안한다(결정은 CTO):

1. J3(페이퍼 주문)·J8(모의→실운용 전환) 관련 리스크 게이트 경로 — 돈이 걸린 화면이 체감 신뢰도에
   가장 직접 영향.
2. CH(차트)·BT(백테스트) 성능 경로 — D2 체감 성능 측정 코드가 아직 없는 축이라 D3 심화 이전에
   측정 계측부터 필요.
3. DC(데이터 커버리지) — 벤더 확장 시마다 회귀 위험이 커 안정화 단계에서 우선순위가 높다.
4. MP(마켓플레이스)·SIG(신호 유입) — 보류 축이라 D2/D3 논의 자체가 아직 해당 없음(N/A, 착수 전).

## Consequences
- 기존 MVP-1 종결 지표([[ADR-2026-09-26-C]])와 깊이 하한([[ADR-2026-09-09-C]])은 변경 없음 —
  둘 다 신뢰성 하한으로 계속 유지·심화한다.
- 이 ADR이 Accepted되면 D1(체감 성능 계측)·D3(J7~J9 CTA 연결)·D4(성과 고지 문구) 각각에 대해
  별도 구현 리프를 발행해야 한다 — 이 ADR 자체는 리프를 발행하지 않는다(문서 전용).
- `docs/ADR_INDEX.md`는 `scripts/gen_adr_index.py` 재실행으로 갱신했다.

## Rejected
- 기존 D2/D3 깊이 기준을 사용자 관점 지표로 **대체**하는 안: 사용자 지시가 "기존 지표는 그대로
  유지·심화, 이 ADR은 추가"라고 명시했으므로 기각.
- 체감 성능 목표값을 이 ADR에서 새로 제정하는 안: 기존 `UX_JOURNEYS.md` §4와
  [[ADR-2026-09-09-C]] 성능 예산에 이미 있는 값을 재사용하고, 이 ADR은 "측정 코드가 없다"는 실사
  결과만 기록한다 — 근거 없는 수치를 만들지 않기 위함(task 지시 "금지: 근거 없는 수치").
