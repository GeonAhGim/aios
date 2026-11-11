# 명세 리프 진행 현황 (자동 생성 — C:/aios/pm/spec_status.py, 편집 금지)

갱신: 2026-11-11T18:15:35+00:00

| 명세 | 총 | done | 재오픈 | inflight | untouched | hold | done% | 깊이(done) |
|---|---|---|---|---|---|---|---|---|
| ai_research_strategy_factory | 23 | 23 | 0 | 0 | 0 | 0 | 100% | {'D1': 7, 'D2': 12, 'D3': 4} |
| analytics_authoring_backtest_marketplace | 107 | 90 | 5 | 0 | 0 | 17 | 84% | {'D1': 1, 'D2': 31, 'D3': 45, 'D?': 13} |
| compliance_and_regulatory | 20 | 19 | 0 | 0 | 0 | 1 | 95% | {'D2': 1, 'D3': 18} |
| ems_routing_algos_and_tca | 18 | 18 | 0 | 0 | 0 | 0 | 100% | {'D1': 1, 'D2': 6, 'D3': 10, 'D?': 1} |
| execution_oms_and_exchange | 30 | 30 | 2 | 0 | 0 | 0 | 100% | {'D1': 3, 'D3': 27} |
| execution_ownership_and_safety_gate_wiring | 6 | 6 | 1 | 0 | 0 | 0 | 100% | {'D3': 6} |
| ibor_fund_accounting_and_resilience | 29 | 29 | 0 | 0 | 0 | 0 | 100% | {'D3': 29} |
| market_data_positions_ledger | 63 | 63 | 0 | 0 | 0 | 0 | 100% | {'D1': 3, 'D2': 1, 'D3': 57, 'D?': 2} |
| mvp3_t1_operations | 11 | 0 | 0 | 0 | 0 | 11 | 0% |  |
| platform_observability_tenancy_api | 42 | 42 | 0 | 0 | 0 | 0 | 100% | {'D1': 9, 'D2': 8, 'D3': 22, 'D?': 3} |
| product_experience_and_discovery | 22 | 22 | 0 | 0 | 0 | 0 | 100% | {'D2': 15, 'D3': 4, 'D?': 3} |
| research_data_and_market_ecosystem | 18 | 18 | 0 | 0 | 0 | 0 | 100% | {'D1': 1, 'D2': 9, 'D3': 8} |
| risk_and_safety | 58 | 53 | 1 | 0 | 0 | 5 | 91% | {'D1': 4, 'D3': 49} |
| strategy_portfolio_backtest | 46 | 46 | 0 | 0 | 0 | 0 | 100% | {'D1': 5, 'D2': 9, 'D3': 30, 'D?': 2} |
| **합계** | 493 | 459 | 9 | 0 | 0 | 34 | 93% | |

상태 정의: done = 해당 ID를 제목/명세에 포함한 implement task가 done(재오픈 = done인데 열린 task도 있음: DEEPEN·묶음 QA), inflight = 열린 implement task만 존재, untouched = task 미발행, hold = spec_hold.yaml. depth = QA task note의 `depth=D<n>` 최대값(ADR-2026-09-09-C: done 하한 D2, 안전축 D3). D? = QA 등급 미기록.
