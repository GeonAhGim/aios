# 06. Phase 1 MVP 스콥 정의서 — v1.3

> **v1.3(2026-08-28) = 다자산군(Multi-Asset-Class) 확장 라운드.** ADR-2026-08-28
> 반영 — "타입 체계가 지원하는 범위"와 "Phase 1에서 실제로 실거래하는 범위"를
> 분리했다. §6.1에 다자산군 원칙 행 추가, §6.1-A(신규)에 자산군별 현재 상태
> 표 신설. **실거래 대상 자체(Bitget 크립토)는 바뀌지 않는다** — 데이터
> 모델·Adapter 인터페이스(01/02번)가 국내/해외 주식·선물·옵션·ETN/ETF까지
> 표현 가능해졌을 뿐, 그것들을 실제로 사고파는 것은 각 자산군의
> ExchangeAdapter가 구현되고(작업트리 6번 확장) §6.3 DoD를 통과한 이후다.

> **v1.1(2026-08-10) = "0번부터 재검토" 라운드 — 번호 충돌 정정.** §6.1~6.5가
> 정책문서(docx) 6장과 번호가 겹치는 것을 발견 — 08/09번과 동일 라운드에
> 동일 조치.
> **v1.2(2026-08-10)**: §6.3/§6.4 자기모순 실제 병합 완료 — 이전 라운드에
> `patch-06-mvp-scope-contradiction.md`로만 존재하고 이 문서 본문에는
> 반영되지 않았던 것을 "산출물 최종 점검" 단계(01번과 동일 문제 재발견)에서
> 발견해 완결. ①§6.3 FD-10.1 완료조건의 "20.1-B 승인권자 확정 후"라는
> ADR-2026-08-10 이전 표현 정정. ②§6.4 "Strategy Marketplace 관련 일체
> (Phase 5)"가 §6.1과 정반대로 서술하던 자기모순을 정책원문(12.2) 근거로
> 정밀하게 재정의.

> 근거: 12.1(Phase 1 로드맵), 15.6-A(Zone 분류), 2026-08 스콥 확정 대화
> 목적: "무엇이 Phase 1에 포함되고 포함되지 않는지"를 애매함 없이 고정한다.

## §6.1 확정된 스콥

| 항목 | 확정값 |
|---|---|
| 대상 심볼 | 5개 내외 (Bitget 유동성 상위 기준 — 6.2 참조, 최종 리스트는 실제 착수 시 확정) |
| 활성 거래소 | **Bitget** (Demo Trading부터, 7.8 확인된 공식 경로) |
| 인터페이스만 대상 | **KIS(한국투자증권)** — Adapter 클래스는 구현하되 실거래·Paper Trading 대상 아님 |
| 보류 거래소 | Bithumb (코드 보존, 착수 대상 아님) |
| 지원 주문 타입 | 시장가(Market) + 지정가(Limit) + TWAP 분할(8.3-A Execution Strategy) |
| 미지원 주문 타입(Phase 1) | VWAP, Iceberg, SOR(다중 거래소 분할) — 8.3-A에 정의는 있으나 Phase 1 구현 대상 아님 |
| 자산군 | crypto 단일 (KIS는 인터페이스만이므로 실제 kr_equity 거래는 없음 — 8.6-A Cross-Asset 문제 발생 안 함) |
| Zone | SCAFFOLD만 (FROZEN은 15.6-D 종료조건 전까지 인터페이스만) — 단, 안전장치(Watchdog 등)는 v3.1 재분류로 SCAFFOLD(12번 §FD-9) |
| **사용자 규모** (v3.1 추가) | **초기 2~3인 → MVP 10인, 처음부터 멀티테넌시 구조**(13번 문서). 심볼 5개·거래소 2개는 시스템 공통 화이트리스트로 유지 — 사용자별 상이한 심볼셋 지원은 Phase 1 스콥 아님 |
| **마켓플레이스** (v3.1 추가) | P2P 골격(리스팅·구매 테이블)은 Phase 1 DB에 포함하되, 실제 리스팅 검증·결제 플로우는 14번(예정)에서 별도 상세화 — Phase 1 Definition of Done에는 미포함 |
| **다자산군 아키텍처** (v1.4 추가, ADR-2026-08-28) | 데이터모델·Adapter 인터페이스(01/02번)는 크립토·국내외 주식·국내외 선물옵션·국내외 ETN/ETF를 표현 가능한 공통 상위집합으로 설계 — 단, **Phase 1 실거래 대상은 여전히 Bitget 크립토뿐**(§6.1-A 참조). 자산군별 실제 실거래 확장은 각 ExchangeAdapter가 capability를 선언하고 개별적으로 DoD를 통과하는 시점마다 순차 확정 |

## §6.1-A 자산군별 현재 상태 (신규, ADR-2026-08-28)

| 자산군 | 브로커(예정) | Phase 1 상태 |
|---|---|---|
| CRYPTO | Bitget | **실거래**(Demo→실계좌, §6.1 그대로) |
| KR_EQUITY | KIS | 인터페이스+조회성 API까지(§6.1 그대로, 매매는 FROZEN 이후) |
| US_EQUITY | KIS(확인 필요) | Draft — KIS 실제 지원 범위는 KISAdapter 착수 시(작업트리 6번) 공식 문서로 확인 후 확정 |
| KR_FUTURES / KR_OPTION | KIS(확인 필요) | Draft — 상동 |
| KR_ETF / KR_ETN | KIS(확인 필요, 매매 메커니즘은 KR_EQUITY와 동일할 가능성 높음) | Draft |
| OVERSEAS_FUTURES | 확인 필요(KIS 또는 별도 브로커) | Draft — 브로커 자체가 미확정 |
| US_ETF / US_ETN | KIS(확인 필요) | Draft |
| OVERSEAS_OPTION | 미확정 | Draft — 사용자가 명시적으로 요구하지 않았으나 타입 체계 일관성을 위해 예약 |

- 위 표의 "Draft" 항목은 **코드로 표현은 가능하지만 아직 아무 Adapter도
  실제로 구현하지 않은 상태**를 뜻한다 — 02번 §2.0-A capability-gated
  원칙에 따라, Adapter가 `supported_asset_classes`에 선언하지 않은 자산군은
  Validator가 자동으로 거부하므로 "타입은 있지만 실거래는 안 되는" 상태가
  안전하게 유지된다.
- 이 표는 각 브로커의 Adapter가 실제로 착수될 때마다(작업트리 6번 확장)
  갱신한다.

## §6.2 심볼 리스트 (Draft — 확정 필요)

Bitget 유동성·거래량 기준 상위 5개를 기본 후보로 제안한다. 최종 리스트는 실제 코드 착수 직전 재확인(가격변동성·최소주문단위 등 실무 요건 포함):

1. BTC/USDT
2. ETH/USDT
3. SOL/USDT
4. XRP/USDT
5. DOGE/USDT (또는 팀 판단에 따라 변경 가능)

> ⚠️ 이 목록은 Draft다. 확정 전 Bitget 실제 마켓 목록·Tick Size·최소주문금액을 조회해 반영한다(개발명세서 §2 `ExchangeCapability.min_order_size`/`tick_size` 필드에 실제 값 채우기).

## §6.3 Definition of Done — Phase 1 SCAFFOLD 완료 기준

아래를 **모두** 충족해야 Phase 1 SCAFFOLD가 "완료"로 간주된다 (FROZEN 착수와는 별개 — 정책문서 15.6-D 참조):

- [ ] `01_data_models.md`의 모든 Pydantic 모델이 실제 코드로 존재하고 단위 테스트 통과
- [ ] `BitgetAdapter`가 Demo Trading 계정으로 시장가·지정가 주문 전송·취소·조회 성공
- [ ] `KISAdapter`는 인터페이스 구현체 존재(메서드 시그니처 일치), 실제 호출은 모의투자 계정으로 조회성 API(시세·잔고)까지만 검증 — 매매는 FROZEN 이후
- [ ] `InProcessEventBus`가 최소 3개 토픽(`market.ticker.updated`, `order.status.changed`, `audit.decision.logged`)으로 실제 publish/subscribe 동작
- [ ] `Loader/Parser/Validator/Scanner` 4개 모듈 함수 전부 구현 + 단위 테스트
- [ ] `audit_log` 테이블에 WORM 제약(`REVOKE UPDATE, DELETE`) 적용 확인
- [ ] 정책문서 8.6-A-1-1 Watchdog 오탐 시뮬레이터 최초 1회 실행(수치 통과 여부와 무관하게 "실행 자체"는 SCAFFOLD 완료 조건, 통과는 FROZEN 착수 조건 — 20.1-A A그룹 참조)
- [ ] Watchdog 판정 로직(FD-9.2)·Circuit Breaker 상태기계(FD-9.4)·Data Distrust 쿼럼(FD-9.5) 구현 및 단위 테스트 통과 (v3.1 Zone 재분류 반영 — 12번 문서 §FD-9)
- [ ] Critical Risk 승인 요청 워크플로(FD-10.1) — **SOLO 모드는 본인 1계정으로 즉시 테스트 가능(사용자 레벨, 20.1-B 플랫폼 블로커와 무관 — ADR-2026-08-10 확정), DUAL 모드는 Mock 승인자 2계정으로 왕복 테스트**(v1.1 병합 — 기능설계문서 v1.5에서 FD-10.1 자체는 이미 정정됐으나 이 원문에는 소급 반영이 안 됐던 것을 "0번부터 재검토" 라운드에서 발견해 완결)
- [ ] 8.2-D 지연 벤치마크 최초 측정 (목표 미달이어도 측정 자체는 완료 조건)

## §6.4 명시적 제외 (Phase 1에 포함되지 않는 것)

혼동 방지를 위해 "안 하는 것"을 명시한다:

- Strategy/Portfolio/Risk/Executor의 실제 판단 로직 **중 LIVE 경로**(PAPER는 ADR-2026-08-29-E로 개방됨 — FROZEN-PAPER-ONLY, 정책문서 15.6-D 조건 2 충족 전까지 LIVE만 계속 제외)
- AIOS Kernel 14개 모듈의 오케스트레이션 로직 (Phase 4)
- Bithumb, SK증권 연동
- KIS 실제 매매(주문 전송) — 인터페이스·조회성 API까지만
- Multi-Agent Debate, Sentiment Agent 등 자문 계층 전체 (정책문서 9.12, 5.4)
- Strategy Marketplace의 **완성형만**(정책문서 12.2 정의: 자동 평판시스템[9.5-A
  Black/Killer Team 완전자동화, 14번 문서 §14.3], B2B, 대규모 트래픽 대응) —
  MVP 골격(리스팅·구매·수동검증·MVP특화 리뷰·검색·분쟁처리, FD-13.1~13.10)은
  Phase 1 포함이 정책 원문(12.2)과 정확히 일치함(v1.1 병합 — 이전엔 "Strategy
  Marketplace 관련 일체(Phase 5)"라고 §6.1과 정반대로 서술돼 있던 자기모순을
  "0번부터 재검토" 라운드에서 발견해 완결. 원본 패치: patch-06-mvp-scope-contradiction.md)
- VWAP/Iceberg/SOR 등 고급 주문 실행 전략(단, FD-9.2 Watchdog 강제청산은
  정책원문 8.6-A-1의 "분할 실행 불가능 시 즉시 시장가 전환" 조항에 따라
  SOR 없이도 안전하게 동작하도록 이미 폴백 설계됨 — "DevEngine 비교 검토"
  라운드에서 상호 정합성 확인)
- 다중 프로세스/분산 처리 (정책문서 5.4 동시성 모델 참조 — 단일 프로세스로 충분)

## §6.5 Phase 1 SCAFFOLD 완료 후 다음 단계

Definition of Done 충족 → 20.1-A Go/No-Go 체크리스트(A그룹 3개 게이트) 실제 실행 → 통과 시 15.6-D 종료조건 충족 여부 재확인 → FROZEN Zone(Strategy/Portfolio/Risk/Executor) 착수 논의.
