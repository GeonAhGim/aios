# L4 구현 명세 (기관·자산운용사급)

사용자 지시(2026-09-03): "기능이 기초 단계가 아니라 엔터프라이즈급 이상, 기관이나
자산운용사가 활용하는 수준의 퍼포먼스를 내도록 개발 문서를 최소단위 모듈로
세분화·정밀화·고도화한다." 각 문서는 `_TEMPLATE.md`의 §0~§10 구조를 따르며,
§2 모듈 분해(파일당 한 책임, ≤300줄)와 §9 리프 목록(리프 = 커밋)이 실행 단위다.

| 문서 | 범위 | 모듈 / 리프 | 주요 신규 발견 |
|---|---|---|---|
| `L4_risk_and_safety_v1.0.md` | 사전 리스크 8지표 정식화, VaR/ES 통계, 킬스위치 단일 권위·fence, CB 지표·재가동, 청산 슬라이서, DataDistrust, RiskDecision 48번 1:1, DB 트리거로 Master Authority | ~70 / 58 | 상관계수 미지 쌍 0.0 fail-open, VaR σ 단위 불일치, `data_delay_sec` 상수 0 |
| `L4_execution_oms_and_exchange_v1.0.md` | 주문 상태기계(DB 강제), 전역 멱등·안정 client id, outbox/inbox, TWAP/VWAP/POV/iceberg, 전송 정책·WS 세션, PAPER 시뮬레이터, 3-way 리컨실, UNKNOWN 해소 | 52 / 30 | 전송 실패 시 claim 삭제 → 고아 주문, 미지 오류 일괄 Retryable |
| `L4_strategy_portfolio_backtest_v1.0.md` | 조건트리 v2·상태메모리 영속화·사이징·리밸런싱·이벤트드리븐 백테스트·DSR/PBO·검증 FAIL 경로·성과보고서(81번) | ~75 / 50 | 백테스트 O(n²) 지표 재계산, `hard_fail_reasons` 항상 빈 튜플 |
| `L4_market_data_positions_ledger_v1.0.md` | 시장데이터 품질게이트·달력·심볼 수명주기, append-only 포지션 저널, 복식부기 원장·WORM role 분리·홀드/에스크로 | ~95 / 61 | **환불이 돈을 생성**(레드팀 #41, `9ce7cc9`로 수정) |
| `L4_platform_observability_tenancy_api_v1.0.md` | RequestContext 관통, 응답 봉투·에러 taxonomy, JWT jti/refresh/세션, 조직 테넌시+RLS, 키 버전·회전, break-glass, 메트릭 24·알림 11·runbook 8 | ~60 / 42 | `trace_id`가 호출마다 새 uuid, 로그아웃 no-op |
| `L4_execution_ownership_and_safety_gate_wiring_v1.0.md` | 실행 리스/펜싱(P0-R1), kill switch·DataDistrust 게이트 배선(P0-R2/R3) | EO-01~06 전량 구현(ADR-2026-09-04-C 승인) | Executor 경로 게이트 우회·xfail 무력화 등 P0 6건은 ADR-2026-09-06-E로 추가 종결 |
| `L4_analytics_authoring_backtest_marketplace_v1.0.md` | 데이터 커버리지(DC)·차트(CH)·지표(IND)·AIOS Script(DSL)·백테스트 현실성(BT)·마켓플레이스(MP)·신호 유입(SIG). ADR-2026-09-04-B. | §9 리프 PM 배정 중 | ADR-2026-09-04-B·D, 2026-09-06-C·F |
| `L4_ai_research_strategy_factory_v1.0.md` | Agent Gateway(MCP·스코프 토큰)·모델 공급자(Claude/OpenAI·Codex/Gemini/로컬)·Strategy Factory·Experiment Ledger·ML Signals. ADR-2026-09-05-A. | §9 리프 PM 배정 중 | ADR-2026-09-05-A |
| `L4_research_data_and_market_ecosystem_v1.0.md` | 리서치 데이터 플레인(공시·뉴스·거시·대안). 포트 먼저·소스는 어댑터. ADR-2026-09-06-A. | §9 리프 PM 배정 중 | ADR-2026-09-06-A |
| `L4_ibor_fund_accounting_and_resilience_v1.0.md` | 엔티티 계층(법인·펀드·포트폴리오·배분)·양시간축 장부(IBOR/ABOR)·이벤트 재현·HA/DR·KMS. ADR-2026-09-06-B. | §9 리프 PM 배정 중 | ADR-2026-09-06-B·E |
| `L4_compliance_and_regulatory_v1.0.md` | 리스크와 분리된 컴플라이언스 권위(사전·사후), 위임장, 규칙 번들 거버넌스, 규제 보고. ADR-2026-09-06-B. | §9 리프 PM 배정 중 | ADR-2026-09-06-B·E |
| `L4_ems_routing_algos_and_tca_v1.0.md` | 부모-자식 주문·벤처 라우팅·집행 알고리즘(TWAP/VWAP/POV/IS)·거래비용분석·FIX 포트. ADR-2026-09-06-B. | §9 리프 PM 배정 중 | ADR-2026-09-06-B·E |

## 실행 규칙
- 리프는 §9 순서대로, 한 리프 = 한 커밋, 각 리프에 negative test 1개 이상.
- FROZEN_PAPER_ONLY(`src/core/strategy|portfolio|risk|executor`) 리프는 ★ 표시 — PM 승인(사용자 위임, 2026-09-03) 후 착수.
- 마이그레이션은 PM이 체인을 직렬화한다(`docs/FULL_AUDIT_2026-09-02.md` §2-B).
- 세션 배정은 §2-B 표가 진실. 명세의 "타 세션, 미커밋" 표기는 작성 시점 스냅샷이다.
- 미확정(§10) 항목은 구현 전에 공식 문서로 확인하거나 "미검증"으로 코드에 남긴다.

## 연구 산출물 이력 (2026-09-03 → 승인)

위 표의 실행 소유권·분석·AI·리서치데이터·IBOR·컴플라이언스·EMS·제품경험 명세 8건은 원래 Codex-Fable 교차검토 체계의
"내부 아키텍처 감사" 역할이 제안 설계로 낸 것이다. ADR-2026-09-04-C가 승인해 §9 리프를 `pm/tasks/*.json`으로 배정했고,
이후 ADR-2026-09-06-B·E와 ADR-2026-09-08-A가 범위와 거버넌스를 확정했다. 승인 전 문구("미승인 — 작업지시서 아님")는
더 이상 유효하지 않으므로 이 절에서 제거했다. 원문 스냅샷은 `docs/research/brainstorm_2026-09-03/`에 동결 보관한다.

