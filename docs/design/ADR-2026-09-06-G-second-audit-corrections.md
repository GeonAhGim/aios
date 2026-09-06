# ADR-2026-09-06-G: 2차 전면 감사 — 품질 조립선, 벤더 중복, 수준 격차

## Status
Accepted (2026-09-06, Chief Architect). 사용자 지시: "다른 부분들도 비효율적이거나 수준이 낮은 것들이 있는지 찾아보고 업데이트하라."

## 방법
다섯 갈래 병렬 감사. (1) 저장소 중복, (2) 오픈소스 차용 가능성(라이선스 실사 포함),
(3) 안전·실행 명세 품질, (4) 데이터·원장·회계 명세 품질, (5) 컴플라이언스·EMS·플랫폼·AI 명세 품질.
모든 발견에 `file:line` 증거를 요구했다. ADR-E·F에서 이미 정리된 항목은 재확인만 하고 다시 열지 않았다.

## 핵심 결론
**ADR-E가 찾은 "게이트는 있는데 조립선이 끊겼다"가 품질 층에서 그대로 반복되고 있었다.**
`scripts/` 아래 품질 검사 5종은 자기 단위테스트 말고는 아무도 호출하지 않았고, GitHub Actions는 과금 잠금으로
돌지 않으므로 실제 강제 지점은 `pm/local_ci.py` 하나인데 거기에 그 다섯이 빠져 있었다.
배선하자마자 두 건이 적색이 됐다 — 아무도 안 보는 사이 조용히 어긋난 것들이다.

---

## 1. 지금 반영한 것 — 품질 조립선 (완료)

| 항목 | 상태 | 증거 |
|---|---|---|
| `check_migration_chain`·`check_openapi_compat`·`check_type_ignore_budget`·`check_zone_diff` 미호출 | **배선 완료** (`pm/local_ci.py` steps) | 저장소 전수 grep 결과 호출부 0 |
| pytest에 커버리지 없음 → `coverage_ratchet` 무의미 | **`--cov=src --cov-report=xml` + 래칫 배선** | `local_ci.py` 이전 steps |
| 프런트엔드가 `build`만 검사 | **`lint`·`test` 추가** (oxlint 통과, vitest 607건 통과) | 시험 실행 |
| OpenAPI 기준선 노후 330건 | **재생성 후 통과** (커밋 `60e0b8d`) | 봉투 도입 `afc0772`, 기준선 미갱신 2026-09-03 |
| `type: ignore` 85 → 170 | **예산 재설정 + 감축 리프 신설** | `check_type_ignore_budget` |

**원칙 추가 (I-10 보강)**: *한 번도 실패한 적 없는 게이트는 통과 중이 아니라 안 돌고 있을 가능성이 높다.*
새 검사 스크립트를 만드는 리프의 DoD에는 **그 스크립트가 `local_ci.py` steps에 등록됐다는 사실**을 포함한다.

---

## 2. 지금 반영한 것 — P0급 실코드 결함

### D0. FA-2가 FA-0a가 고치려던 결함을 오늘 재생산했다
`src/db/migrations/versions/e6b1d94a7c3f_fa2_entities_hierarchy.py:53`이
`legal_entity.tenant_id UUID NOT NULL REFERENCES users(user_id)`를 만들었다.
실제 테넌트 테이블은 `f4a6b8c0d2e4:27`의 `tenant`다. ADR-E가 FA-0a를 신설한 바로 그 결함의 **20번째 사례**이며,
하필 엔티티 계층의 **최상위 테이블**이다. 원인은 명세의 선행 누락이다 — FA-3/FA-4만 FA-0a에 걸려 있고 FA-2는 안 걸려 있었다.

**결정**: FA-2의 선행에 FA-0a를 추가하고, `legal_entity.tenant_id`를 `tenant(tenant_id)`로 교정하는 리프를 만든다.
오늘 만든 테이블이라 지금이 가장 싸다.

---

## 3. 차용 우선 원칙의 2차 적용 — 이미 반입한 벤더를 다시 만들고 있다

ADR-F D4의 순서를 CH 축에 적용하지 않은 결과가 남아 있었다.
`frontend/packages/chart-engine/vendor/klinecharts/`(Apache-2.0)는 이미 다음을 내장한다:
드로잉 오버레이 **17종**(`extension/overlay/`), 툴팁·크로스헤어 뷰(`view/CandleTooltipView`·`IndicatorTooltipView`·
`IndicatorLastValueView`·`CrosshairFeatureView`), 지표 **27종**과 `registerIndicator`/`IndicatorTemplate` 플러그인 API.

| 리프 | 현재 계획 | 결정 |
|---|---|---|
| CH-4 | `drawings/{model,tools,serialize}.ts` **709줄 수기**로 5종 재구현 | **QA 지적 사항으로 전환** — `chart.createOverlay()`/`registerOverlay()` 위 얇은 어댑터로 축소. 병행 모델 유지 시 피보나치 구현이 둘로 갈라진다 |
| CH-16 | 범례·데이터윈도우·오브젝트트리 신규 | **축소** — 툴팁·크로스헤어는 벤더 뷰 스타일 바인딩, `objectTree.ts`만 신규 |
| CH-11·CH-15 | 지표 플러그인·플롯 렌더러 신규 | **축소** — PlotSpec→`IndicatorTemplate` 컴파일러 + 타입 파사드. 진짜 신규는 `fillBetween` 하나 |

`NOTICE-AIOS.md`에 **포크 지점을 고정 기록**한다. KLineChart v10은 지표·오버레이·축을 파괴적으로 재편했으므로
맹목 리베이스를 금지한다.

### IND-10과 IND-2g가 같은 생성기다
IND-10(`§9.9`, `adapters/talib_bridge.py`)과 ADR-F가 만든 IND-2g(`§9.3`, `catalog/generate_from_talib.py`)가
둘 다 `talib.get_functions() × abstract.Function(n).info`로 161종 스펙을 생성한다.
IND-11·IND-7g의 선행은 IND-2g다. **IND-2g가 정본**이고 IND-10은 얇은 재노출로 축소하거나 폐기한다.

---

## 4. 라이선스 게이트 정정 — 배지만 보면 통과하는 함정

기존 게이트는 "GPL/LGPL 금지"만 적혀 있었다. 실사 결과 **배지가 허용 라이선스로 보이지만 실제로는 금지**인 부류가 있다.
`docs/design/INDICATOR_OSS_EVAL.md`(IND-9)에 아래를 명시적 거부 등급으로 추가한다.

| 등급 | 사례 | 사유 |
|---|---|---|
| **Apache-2.0 + Commons Clause** | vectorbt, pybroker | 소프트웨어 가치에 실질적으로 기대는 제품의 **판매를 금지**한다. AIOS가 정확히 그 경우다. 배지에는 Apache-2.0만 보인다 |
| **BSL 1.1** | ArcticDB | 상용·운영 사용에 Man Group의 유상 라이선스 필요. "오픈소스"로 홍보된다 |
| **Timescale License (TSL)** | TimescaleDB의 압축·컬럼스토어·연속집계 | Apache-2.0인 것은 하이퍼테이블뿐. 쓰고 싶어지는 기능이 전부 TSL이다 |
| AGPL/GPL/LGPL | backtesting.py, backtrader, NautilusTrader, beancount, hledger, Hydra | 기존 금지 유지 |

**목록 밖이지만 허용 판정** — `pgmq`(PostgreSQL 라이선스, 실질 BSD-2) 허용.
**목록 밖이며 거부** — `cryptofeed`(XFree86-1.1, 표기 조항), `RestrictedPython`(ZPL-2.1, 게다가 우리 DSL에 불필요),
`lightweight-charts`(Apache-2.0이나 **사용자 화면에 TradingView 표기 의무**, 이미 KLineChart 선정 완료).

## 5. 채택 결정 (ADOPT)

| 대상 | 라이선스 | 용도 | 비고 |
|---|---|---|---|
| `mcp` 공식 Python SDK | MIT | RD-16, 에이전트 게이트웨이 | 프로토콜을 손으로 짤 이유가 없다. 이 감사에서 가장 확실한 채택 |
| `i18next` + `react-i18next` | MIT | UX-1·UX-2 | **UX-2를 코드모드로 재정의**한다(수기 치환 금지, 재실행 가능) |
| `holidays` | MIT | 공휴일 집합만 | 세션 규칙(KRX 09:00–15:30·종가단일가)은 수기 — 어떤 라이브러리도 맞지 않는다 |
| `pyarrow` | Apache-2.0 | BT-10 컬럼 경로, LA-24 리플레이, RD-4/7 | **리프별 정당화 필수**. 설치 40~90MB |
| `duckdb` | MIT | RD-7 PIT 질의 **리서치 평면 한정** | 주문 경로 사용 금지(단일 프로세스, R-35의 p99 50ms와 무관) |
| CodeMirror 6 | MIT | DSL-13b 오류 마커 | Monaco(~2~5MB) 대신 ~200KB |
| TanStack Table/Virtual | MIT | EM-18·CM-18·RD-17 대용량 표 | 500행 넘을 때 도입. AG Grid는 원하는 기능이 전부 Enterprise |
| `promptfoo` | MIT | 에이전트 프롬프트 회귀 | dev/CI 전용, Node 쪽 |
| OpenFIGI | MIT(데이터) | RD-5 결정적 엔티티 링크 | 이름 유사도 추측 없이 RD-A4 충족 |

## 6. 참조만 하고 직접 쓴다 (REFERENCE)
zipline-reloaded 슬리피지·수수료(BT-2~6), empyrical/ffn 지표 공식(BT-10·R-29 — 전부 numpy 5~15줄,
pandas를 끌어올 이유 없음), tcapy 벤치마크 정의(EM-12, 2022년 이후 방치), `eventsourcing` PostgresRecorder(FA-13),
Hummingbot `TWAPExecutor`·LEAN 실행 모델(EM-8~11), QuantLib daycounters, CCXT 마켓 메타데이터(DC-8).

## 7. 손으로 쓰는 것이 맞는 곳 (정직한 결론)
**OMS 아웃박스**: pgmq·PgQueuer·procrastinate 전부 `SKIP LOCKED` at-least-once일 뿐 **펜싱 토큰이 없다**.
리스를 잃은 스테일 워커가 여전히 쓸 수 있으므로 `test_stale_worker_late_write`가 잡으려는 실패를 못 막는다.
큐 라이브러리를 감싸고 펜싱을 덧대는 편이 SQL 200줄보다 크다. **이 축에서는 "적극 차용"이 틀린 직관이다.**
같은 이유로: 바 매그니파이어(BT-7), 펀딩·차입 이자(BT-8), TCA 분해(EM-12/13), 복식부기·평균단가(FA-7),
컴플라이언스 규칙 파일(CM-3·6·7·9 — 명세가 원하는 것은 규칙 엔진이 아니라 순수 함수 8개다),
DSL 타입 시스템·룩어헤드 분석·IR 하강(DSL-4/5/7), KRX 세션 캘린더, 기업행위 파싱.

**DSL은 이미 늦었다**: Lark(MIT)가 올바른 도구였으나 DSL-1·2·3이 이미 병합됐고 DSL-4~8이 그 위에 있다.
지금 교체하면 이미 가진 것(DSL-2 DoD "토큰 전 종류 + 오류 위치")을 얻으려 하위 리프 전부를 무효화한다. **거부, 사유 기록.**

---

## 8. 강제 리프가 없던 불변조건 — 신설

| 불변조건 | 문제 | 신설 리프 |
|---|---|---|
| **I-05** 백테스트=라이브 패리티 | DSL-11은 신호엔진 신구 비교, BT-9는 같은 시드 재현성만 증명한다. **PAPER 실행 추적을 리플레이해 체결을 대조하는 리프가 없다** | **BT-19** `backtest/application/parity_harness.py` — PAPER 추적을 같은 아티팩트·구간으로 재생, 체결 순서 바이트 동일(타임스탬프 제외). 180줄 |
| **I-04** 아티팩트 불변 | `REVOKE UPDATE, DELETE` 마이그레이션도 `test_tamper.py`도 **있는데**, 어느 리프 DoD도 그 테스트를 인용하지 않는다 | L37 DoD에 `test_tamper.py::test_artifact_update_revoked` 명시(리프 신설 아님) |
| **CM-A2** 규칙 순수성 | "네트워크·LLM 호출 금지"를 선언만 하고 검사하지 않는다 | **CM-21** `tests/adversarial/compliance/test_rule_purity.py` — 규칙 평가 중 `socket`/`httpx` 접근 시 예외. 120줄 |
| **EM-A4** 종결 부모의 자식 차단 | 포괄 적대적 리프에만 묻혀 있다 | EM-2 DoD에 명시(종결 상태 전수 property test) |
| **I-09** 이중 권위 | `background_loops.py:252`가 `require_mandate=False`로 조립한다 — 컴플라이언스 절반이 감사로그 전용 no-op | **R-59** 두 조립 지점이 `require_mandate=True`임을 단언하거나, 만료일 있는 ADR 예외가 존재함을 단언. 80줄 |

## 9. 수준 격차 — 신설 리프

| 격차 | 동종 제품 | 신설 |
|---|---|---|
| 주문 유형이 MARKET·LIMIT뿐. **스톱·스톱리밋·OCO·트레일링 없음** | EMSX·Charles River는 기본 주문 유형 | **EM-19** `domain/order_types/{stop,stop_limit,oco,trailing}.py` + `trigger_price` 계약 필드. 450줄 |
| **공매도·차입·마진 개념 자체가 없다**(locate 없음) | Aladdin/CRIMS는 locate 없는 공매도를 차단 | **LA-25** `positions/domain/borrow.py` + `pos_borrow_position`. 350줄 |
| 옵션·선물이 열거값에만 있고 만기·롤·승수·그릭스 한도 없음 | Bloomberg AIM은 델타·베가 한도 기본 | MVP-2로 명시 이관(포트만) — 지금은 **명세에 부재를 기록** |
| 백테스트에 기업행위 미반영. `BacktestConfigV2.adjustments{splits,dividends}` 필드는 있는데 **구현 리프가 없다** | TradingView·Bloomberg 기본 | **BT-20** `domain/corporate_actions.py`. 200줄 |
| 현금배당이 원장에 도달하지 않는다(참조데이터만 기록) | 모든 T1 | **LA-25b** `ledger/application/post_corporate_action_cash.py`. 200줄 |
| 마켓플레이스 `reproduced_backtests`를 **소비만 하고 생산하는 리프가 없다** — 판매자 주장 성과를 아무도 재현하지 않는다 | 현 상태는 TradingView보다도 나쁨 | **MP-11** `application/verify_listing_backtest.py` — 불일치 시 `MP_UNVERIFIED_RESULT`. 220줄 |
| RBAC 5역할 평면, **직무분리 원시타입 없음**(CM-5·브레이크글라스가 각자 재발명) | Charles River 기능단위 권한 격자 | **PLT-43** `trust/domain/rules/segregation_of_duty.py`. 200줄 |
| FX가 현물 환산뿐, 기관 고시환율·선물환·헤지 손익 없음 | 모든 T1 | **LB-20** `positions/domain/fx_forward.py`. 300줄 |
| 차트·백테스트 경로가 `entitlement.allowed()`를 호출하지 않는다 | 데이터 라이선스 위반 위험 | CH-2·BT-10 DoD에 entitlement 검사 추가 |

## 10. 그래프·문서 정합성 (P2, 일괄 리프)
- **L36-a의 선행 `L42`가 6행 뒤에 온다** — 자기 선행보다 먼저 빌드되는 리프. 순서 교정.
- **PLT-21b**는 코드 7곳이 권위로 인용하는데 명세에 행이 없다(`PLT-17~21` 묶음행이 삼켰다). 행 분리.
- `IdempotencyScope` **동명이의 2종**이 실제로 공존한다 — OMS 쪽을 `OrderIdempotencyScope`로 개명.
- 명세에 행이 없는 구현 파일: `oms/application/{dispatch_outcome,outbox_commands,outbox_submit,outbox_writes}.py`,
  `core/safety/{reconciliation,watchdog_simulator}.py` → **L4-31** 소급 행.
- 전략 명세 지역 불변조건 `I1~I13`이 전역 `I-01~I-11`과 네임스페이스 충돌 → `STR-I1~I13`.
- **재현 키 정의가 둘**이다. 분석 명세가 "대체한다"고 선언했으나 전략 명세 §3.0은 손대지 않았다 → 전략 명세에 전방 참조 추가.
- OMS 명세 `:85`가 `pre_submit_gate: PreSubmitGate | None`을 문서화한다 — **코드는 이미 비Optional + TypeError 가드**다.
  명세가 코드보다 안전하지 않게 뒤처졌다. 교정.
- CM 명세 §2.3이 §0에서 금지한 `src/foundation/compliance/` 신설을 스스로 한다(`reporting/`). 예외를 §0에 명시.
- CM-3·6·7·9·10의 경로에 패키지 접두어가 없어 워커가 새 트리를 만들 수 있다. 접두어 명시.
- 스케줄러 리프(LA-18·LB-17·LC-16)가 함수 시그니처로 적혀 있으나 코드는 클래스다. 계약 블록 갱신.
- 홀리데이 YAML이 `source: UNVERIFIED` 자리표시자다. 실데이터 확보 리프 필요.

## 11. 약한 DoD 일괄 교정
"화면·negative"(15회), "문서화", "번호 부여", "포트 계약 확정" 류는 반증 불가능하다.
**규칙**: 모든 DoD는 *하나의 구체적 거부 입력*이나 *수치 임계*를 포함해야 한다.
가장 비싼 것부터: R-58(적발 6건에 회귀 테스트 요구), L4-30(잘못된 주문 거부까지), FA-20/21(RPO/RTO **실측**),
AI-15/16(퍼징 횟수·오탐 0), CH-6(5,000봉 300ms·axe 0), MP-2(HTTP 응답 바이트에 소스 미포함 통합 테스트).

---

## Consequences
- 신설 리프 **12개**(BT-19·BT-20·CM-21·R-59·EM-19·LA-25·LA-25b·LB-20·MP-11·PLT-43·L4-31·FA-0a-fix) 약 2,700줄.
- 축소 리프 **4개**(CH-4·CH-11·CH-15·CH-16) 및 IND-10 흡수 — 약 1,500줄 감소.
- 순증 약 1,200줄. 대신 **오늘 만들어진 최상위 테이블의 FK 결함**과 **품질 게이트 5종 미작동**을 지금 잡는다.
- 채택 9건으로 MCP 프로토콜·i18n·공휴일·컬럼 저장·에디터 마커 작업이 사실상 사라진다.

## Rejected
- 벡터화 백테스트 엔진 차용: 허용 라이선스인 것은 zipline-reloaded뿐이고 pandas를 강제한다. 1개월 M1 43k봉 ≤5s는 numpy로 충분하다.
- 컴플라이언스 규칙 엔진(CEL·JsonLogic) 도입: 명세가 원하는 것은 순수 함수 8개다. 지금 도입하면 `explain()` 재현성이 엔진 버전에 묶인다.
  **다만 테넌트가 직접 규칙을 쓰게 되는 날에는 CEL(cel-python, Apache-2.0)이 정답**임을 CM-3 설계 노트에 지정 후보로 기록한다.
- 파서 생성기(Lark) 교체: 하위 리프 5개를 무효화하고 이미 가진 것을 얻는다.
- Money 타입(py-moneyed) 도입: Decimal 규율과 금전 버그 수정이 이미 끝났다. 지금은 가치가 아니라 변경비용이다.
