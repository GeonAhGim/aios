# 명세 리프 진행 현황 (자동 생성 — C:/aios/pm/spec_status.py, 편집 금지)

갱신: 2026-09-16T17:30:45+00:00

| 명세 | 총 | done | 재오픈 | inflight | untouched | hold | done% | 깊이(done) |
|---|---|---|---|---|---|---|---|---|
| ai_research_strategy_factory | 23 | 12 | 11 | 11 | 0 | 0 | 52% | {'D2': 1, 'D?': 11} |
| analytics_authoring_backtest_marketplace | 107 | 87 | 8 | 3 | 0 | 17 | 81% | {'D1': 1, 'D2': 27, 'D3': 19, 'D?': 40} |
| compliance_and_regulatory | 20 | 19 | 2 | 0 | 0 | 1 | 95% | {'D2': 6, 'D3': 12, 'D?': 1} |
| ems_routing_algos_and_tca | 18 | 16 | 1 | 2 | 0 | 0 | 89% | {'D2': 5, 'D3': 2, 'D?': 9} |
| execution_oms_and_exchange | 30 | 30 | 1 | 0 | 0 | 0 | 100% | {'D0': 1, 'D2': 1, 'D3': 19, 'D?': 9} |
| execution_ownership_and_safety_gate_wiring | 6 | 6 | 0 | 0 | 0 | 0 | 100% | {'D2': 1, 'D?': 5} |
| ibor_fund_accounting_and_resilience | 29 | 22 | 1 | 7 | 0 | 0 | 76% | {'D2': 2, 'D3': 12, 'D?': 8} |
| market_data_positions_ledger | 63 | 63 | 2 | 0 | 0 | 0 | 100% | {'D2': 4, 'D3': 5, 'D?': 54} |
| platform_observability_tenancy_api | 42 | 42 | 7 | 0 | 0 | 0 | 100% | {'D1': 2, 'D2': 6, 'D3': 3, 'D?': 31} |
| product_experience_and_discovery | 22 | 14 | 0 | 8 | 0 | 0 | 64% | {'D2': 12, 'D?': 2} |
| research_data_and_market_ecosystem | 18 | 6 | 0 | 12 | 0 | 0 | 33% | {'D2': 2, 'D3': 2, 'D?': 2} |
| risk_and_safety | 58 | 53 | 2 | 0 | 0 | 5 | 91% | {'D3': 27, 'D?': 26} |
| strategy_portfolio_backtest | 46 | 38 | 5 | 8 | 0 | 0 | 83% | {'D2': 4, 'D?': 34} |
| **합계** | 482 | 408 | 40 | 51 | 0 | 23 | 85% | |

상태 정의: done = 해당 ID를 제목/명세에 포함한 implement task가 done(재오픈 = done인데 열린 task도 있음: DEEPEN·묶음 QA), inflight = 열린 implement task만 존재, untouched = task 미발행, hold = spec_hold.yaml. depth = QA task note의 `depth=D<n>` 최대값(ADR-2026-09-09-C: done 하한 D2, 안전축 D3). D? = QA 등급 미기록.
