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

## 실행 규칙
- 리프는 §9 순서대로, 한 리프 = 한 커밋, 각 리프에 negative test 1개 이상.
- FROZEN_PAPER_ONLY(`src/core/strategy|portfolio|risk|executor`) 리프는 ★ 표시 — PM 승인(사용자 위임, 2026-09-03) 후 착수.
- 마이그레이션은 PM이 체인을 직렬화한다(`docs/FULL_AUDIT_2026-09-02.md` §2-B).
- 세션 배정은 §2-B 표가 진실. 명세의 "타 세션, 미커밋" 표기는 작성 시점 스냅샷이다.
- 미확정(§10) 항목은 구현 전에 공식 문서로 확인하거나 "미검증"으로 코드에 남긴다.
