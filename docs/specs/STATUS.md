# 명세 리프 진행 현황 (자동 생성 — C:/aios/pm/spec_status.py, 편집 금지)

갱신: 2026-09-15T17:30:22+00:00

| 명세 | 총 | done | 재오픈 | inflight | untouched | hold | done% | 깊이(done) |
|---|---|---|---|---|---|---|---|---|
| ai_research_strategy_factory | 23 | 1 | 1 | 22 | 0 | 0 | 4% | {'D?': 1} |
| analytics_authoring_backtest_marketplace | 107 | 86 | 57 | 4 | 0 | 17 | 80% | {'D1': 1, 'D2': 14, 'D3': 3, 'D?': 68} |
| compliance_and_regulatory | 20 | 15 | 4 | 4 | 0 | 1 | 75% | {'D2': 3, 'D3': 8, 'D?': 4} |
| ems_routing_algos_and_tca | 18 | 14 | 14 | 4 | 0 | 0 | 78% | {'D2': 3, 'D3': 2, 'D?': 9} |
| execution_oms_and_exchange | 30 | 30 | 4 | 0 | 0 | 0 | 100% | {'D0': 1, 'D2': 1, 'D3': 19, 'D?': 9} |
| execution_ownership_and_safety_gate_wiring | 6 | 6 | 2 | 0 | 0 | 0 | 100% | {'D2': 1, 'D?': 5} |
| ibor_fund_accounting_and_resilience | 29 | 22 | 20 | 7 | 0 | 0 | 76% | {'D2': 2, 'D3': 11, 'D?': 9} |
| market_data_positions_ledger | 63 | 63 | 8 | 0 | 0 | 0 | 100% | {'D2': 3, 'D3': 1, 'D?': 59} |
| platform_observability_tenancy_api | 42 | 40 | 37 | 2 | 0 | 0 | 95% | {'D1': 2, 'D2': 2, 'D3': 3, 'D?': 33} |
| product_experience_and_discovery | 22 | 4 | 0 | 18 | 0 | 0 | 18% | {'D?': 4} |
| research_data_and_market_ecosystem | 18 | 5 | 3 | 13 | 0 | 0 | 28% | {'D?': 5} |
| risk_and_safety | 58 | 53 | 3 | 0 | 0 | 5 | 91% | {'D3': 27, 'D?': 26} |
| strategy_portfolio_backtest | 46 | 25 | 24 | 21 | 0 | 0 | 54% | {'D2': 4, 'D?': 21} |
| **합계** | 482 | 364 | 177 | 95 | 0 | 23 | 76% | |

상태 정의: done = 해당 ID를 제목/명세에 포함한 implement task가 done(재오픈 = done인데 열린 task도 있음: DEEPEN·묶음 QA), inflight = 열린 implement task만 존재, untouched = task 미발행, hold = spec_hold.yaml. depth = QA task note의 `depth=D<n>` 최대값(ADR-2026-09-09-C: done 하한 D2, 안전축 D3). D? = QA 등급 미기록.
