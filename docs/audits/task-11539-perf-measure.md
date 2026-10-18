# task-11539 PERF-measure 점검표

완료 전환 337건 / 파일 314개.
169건은 요청 당시 수치이며 현재 완료 목록 전체를 포함했다. 정적 후보와 확정 결함을 구분한다.
단위·예산 변경 원문은 동명 JSON의 budget_history, 호출 위치는 single_call_lines에 보존했다.
0 수렴/주입 적색은 동적 미검증이면 통과로 판정하지 않는다. 파일별 후속에서 확인한다.

| 파일 | 단위 | batch 의심 위치 | 0 수렴 | gate-red | 예산 | 정정 |
|---|---|---|---|---|---|---|
| tests/adversarial/auth/test_signup_tenant_atomicity.py | requires assertion-level review | [165] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/adversarial/ems/test_child_order_bypass.py | requires assertion-level review | [405] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/adversarial/ems/test_overcommit_and_terminal_races.py | requires assertion-level review | [428] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/adversarial/execution_ownership/test_no_double_tick.py | requires assertion-level review | [261] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/adversarial/market_data/test_backfill_job_deepen_perf_d3_2898.py | requires assertion-level review | [100, 146] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/adversarial/marketplace/test_paper_trading_gate.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/api/mcp/test_server_auth.py | requires assertion-level review | [213, 236] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/api/mcp/test_tools_research_data.py | requires assertion-level review | [257, 268] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/contract/nh/test_nh_order_requery_contract.py | requires assertion-level review | [327] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/adversarial/positions/test_race_fills.py | requires assertion-level review | [389] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/ai/gateway/test_confirm_ticket_repository.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/ai/gateway/test_token_lifecycle.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/backtest/test_postgres_snapshot_repository.py | requires assertion-level review | [245] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/ems/test_tca_storage.py | requires assertion-level review | [240] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/experiments/test_application_integration.py | requires assertion-level review | [327] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/experiments/test_postgres_repository.py | requires assertion-level review | [264] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/market_data/test_instruments_schema_deepen_2879.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/market_data/test_quality_metrics_deepen.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/ml/test_postgres_model_registry.py | requires assertion-level review | [214] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/ml/test_postgres_train_job_repository.py | requires assertion-level review | [233] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/performance/test_get_statement_portfolio_scope.py | requires assertion-level review | [310] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/positions/test_position_key_adversarial.py | requires assertion-level review | [201] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/reconciliation/test_reconciliation_resolve.py | requires assertion-level review | [219] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/screener/test_save_share_alert.py | requires assertion-level review | [434] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/trust/test_postgres_repository.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/validation/test_postgres_repository_l37.py | requires assertion-level review | [346] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/validation/test_reproducibility.py | requires assertion-level review | [366, 400] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/integration/validation/test_run_check.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/screener/test_query_plan.py | requires assertion-level review | [165] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/screener/test_run_screen.py | requires assertion-level review | [337] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ai/factory/test_contracts_v1.py | requires assertion-level review | [155, 177] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ai/gateway/test_contracts_v1.py | requires assertion-level review | [228, 285] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ai/providers/test_anthropic_provider.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ai/providers/test_gemini_provider.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ai/providers/test_openai_compatible_provider.py | requires assertion-level review | [367, 397] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ai/providers/test_prompt_registry.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/allocation/test_average_price.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/allocation/test_policy.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/automation/test_evaluate_rules_perf.py | requires assertion-level review | [38] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/backtest/test_cost_models.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/backtest/test_list_bars.py | requires assertion-level review | [174] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/backtest/test_models_v2.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/backtest/test_param_sweep.py | requires assertion-level review | [227] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/backtest/test_reproducibility.py | requires assertion-level review | [211, 277] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/backtest/test_run_backtest.py | requires assertion-level review | [380, 385] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/backtest/test_sandboxed_script_eval.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/backtest/test_simulate_fill.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/backtest/test_stress.py | requires assertion-level review | [42, 74, 123] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/backtest/test_tearsheet.py | requires assertion-level review | [268, 303] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/backtest/test_universe.py | requires assertion-level review | [161] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/backtest/test_walk_forward.py | requires assertion-level review | [54, 91, 138] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/charting/test_indicator_template_application.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/charting/test_layout_application.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/connections/test_begin_connection_application.py | requires assertion-level review | [496] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/connections/test_confirm_connection.py | requires assertion-level review | [378] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/connections/test_fake_provider.py | requires assertion-level review | [107] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/connections/test_projections.py | requires assertion-level review | [271] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/connections/test_provider_port.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/connections/test_revoke_connection.py | requires assertion-level review | [320] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ems/test_compute_tca.py | requires assertion-level review | [265] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ems/test_fix_session_contract.py | requires assertion-level review | [164] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ems/test_route_order_application.py | requires assertion-level review | [244] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ems/test_venue_recalibration.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ems/test_venue_scoring.py | requires assertion-level review | [190, 196] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/entities/test_ensure_default_hierarchy.py | requires assertion-level review | [271] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/entities/test_region.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/evidence/application/test_append_audit_event.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/evidence/application/test_get_audit_timeline.py | requires assertion-level review | [192] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/evidence/application/test_verify_audit_chain.py | requires assertion-level review | [227] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/evidence/test_rules.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/execution_ownership/test_rules.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/experiments/test_lineage.py | requires assertion-level review | [144, 166] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/follow/test_mirror_signal.py | requires assertion-level review | [312] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/market_data/contracts/test_instruments_v2.py | requires assertion-level review | [405] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/market_data/entitlement/test_source_contract_deepen_2888.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/market_data/instruments/test_lifecycle_deepen_2875.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/market_data/instruments/test_symbol_master_deepen_2876.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/market_data/ports/test_ports_protocol_deepen_2877.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/market_data/ports/test_provider_microstructure.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/market_data/providers/test_base_adapter_deepen_2883.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/market_data/providers/test_kis_provider_deepen.py | requires assertion-level review | [204, 205] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/market_data/providers/test_provider_spi_deepen_2885.py | requires assertion-level review | [235, 240] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/market_data/test_read_api_deepen_2888.py | requires assertion-level review | [134, 142] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ml/test_drift.py | requires assertion-level review | [113, 134] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ml/test_feature_values.py | requires assertion-level review | [98] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ml/test_local_trainer.py | requires assertion-level review | [206] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ml/test_point_in_time.py | requires assertion-level review | [117] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ml/test_register_model.py | requires assertion-level review | [277] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ml/test_registry_rules.py | requires assertion-level review | [115] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ml/test_run_train_job.py | requires assertion-level review | [303] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/ml/test_serve_signal.py | requires assertion-level review | [411] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/paper_control/test_contracts_v1.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/paper_control/test_models.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/paper_control/test_projections.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/performance/test_statement_projection.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/positions/test_borrow.py | requires assertion-level review | [400] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/positions/test_fx_forward.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/positions/test_ports_protocol.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/positions/test_restatement.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/positions/test_snapshot_builder.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/positions/test_weighted.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/reconciliation/test_models.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/research_data/test_contracts_v1.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/research_data/test_contracts_v1_deepen_2905.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/research_data/test_known_at.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/research_data/test_known_at_deepen_2905.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/research_data/test_redistribution.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/research_data/test_revision.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/trust/test_evaluate_trust_freshness.py | requires assertion-level review | [290] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/trust/test_models.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/trust/test_projections.py | requires assertion-level review | [203] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/trust/test_segregation_of_duty.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/validation/application/test_compile_artifact.py | requires assertion-level review | [124] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/validation/checks/test_backtest.py | requires assertion-level review | [208] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/validation/checks/test_failure_conditions.py | requires assertion-level review | [199] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/validation/checks/test_oos_walk_forward.py | requires assertion-level review | [315, 342] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/validation/checks/test_point_in_time.py | requires assertion-level review | [197] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/validation/checks/test_robustness.py | requires assertion-level review | [270, 297] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/validation/checks/test_stress_capacity.py | requires assertion-level review | [274] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/validation/test_artifact.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/validation/test_contracts_v1.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/validation/test_contracts_v1_evidence_refs.py | requires assertion-level review | [170] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/validation/test_policy_hash.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/foundation/unit/validation/test_rules.py | requires assertion-level review | [288] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/api/test_dashboard_router.py | requires assertion-level review | [368, 372] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/api/test_health_endpoints.py | requires assertion-level review | [184, 208] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/api/test_idempotency_digest.py | requires assertion-level review | [257, 286] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/api/test_indicators_router.py | requires assertion-level review | [296] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/api/test_market_data_router.py | requires assertion-level review | [223, 232] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/api/test_positions_router.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/api/test_rate_limit_storm.py | ms 일치 | 적응형 batch≥8 | 양수 샘플 강제 | 50ms 주입→62.670225ms, exit=1 | 5ms 불변 | 정정·13 passed×3 (-n 8) |
| tests/integration/api/test_research_data_router.py | requires assertion-level review | [228, 235] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/api/test_research_data_search_router.py | requires assertion-level review | [286, 290] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/api/test_screener_router.py | requires assertion-level review | [271] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/api/test_signup_creates_tenant.py | requires assertion-level review | [171, 175] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/backtest/test_parity_harness.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/core/db/test_rls_force_foundation.py | requires assertion-level review | [104] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/core/eventstore/test_append.py | requires assertion-level review | [231] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/db/test_migration_tenant_membership.py | requires assertion-level review | [299] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/db_schema/test_market_data.py | requires assertion-level review | [363] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/ems/test_algo_lifecycle.py | requires assertion-level review | [203] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/event_bus/test_redis_streams_event_bus.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/eventstore/test_projections.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/eventstore/test_replay_verify.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/allocation/test_allocate_fills.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/entities/test_migration_fa4_deepen.py | requires assertion-level review | [306] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/entities/test_postgres_entity_repository.py | requires assertion-level review | [361, 366, 433, 438, 488] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/entities/test_resolve_context_performance.py | requires assertion-level review | [40, 43] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/entities/test_tenant_fk_enforced.py | requires assertion-level review | [211] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/market_data/test_calendar_repository.py | requires assertion-level review | [251] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/market_data/test_calendar_sync_deepen_2901.py | requires assertion-level review | [182] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/market_data/test_dc13_hot_postgres_deepen_2886.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/market_data/test_dc8_coverage_entitlements_deepen_2884.py | requires assertion-level review | [274] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/market_data/test_get_coverage.py | requires assertion-level review | [405] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/market_data/test_hot_postgres.py | requires assertion-level review | [338] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/market_data/test_instrument_attributes_deepen_2892.py | requires assertion-level review | [140] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/market_data/test_l2_ingest_session_deepen_2902.py | requires assertion-level review | [298] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/market_data/test_perf_replay.py | requires assertion-level review | [335] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/market_data/test_reference_repository.py | requires assertion-level review | [276] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/positions/test_conftest_deepen.py | requires assertion-level review | [165, 190] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/positions/test_legacy_compat.py | requires assertion-level review | [277] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/positions/test_perf_journal_append.py | requires assertion-level review | [254] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/positions/test_pos_borrow_position_schema.py | requires assertion-level review | [295] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/positions/test_rebuild_snapshot.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/positions/test_reconcile_provider.py | requires assertion-level review | [280] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/positions/test_record_funding_fee.py | requires assertion-level review | [267] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/research_data/test_opendart_filing_deepen_2903.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/research_data/test_research_repository.py | requires assertion-level review | [302, 330] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/foundation/trust/test_resolve_tenant_context.py | requires assertion-level review | [316] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/services/auth/test_login_refresh_logout.py | requires assertion-level review | [368] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/services/test_legacy_execution_pauser.py | requires assertion-level review | [283] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/test_admin_break_glass_router.py | requires assertion-level review | [308] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/test_app_lifespan.py | requires assertion-level review | [128] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/test_auth_router.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/test_auth_service.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/test_condition_compiler.py | requires assertion-level review | [130] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/test_db_schema_deepen_2964.py | requires assertion-level review | [121] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/test_event_bus.py | requires assertion-level review | [190] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/test_execution_scheduler.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/test_executor.py | requires assertion-level review | [474] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/test_strategy_builder_service.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/integration/test_task3178_mfa_deepen.py | requires assertion-level review | [172, 222] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/performance/test_pre_trade_latency.py | requires assertion-level review | [224, 122] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/platform/integration/test_break_glass.py | requires assertion-level review | [391] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/platform/unit/test_alert_rules_reference_known_metrics.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/api/schemas/foundation/test_connections.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/api/schemas/foundation/test_paper_control.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/api/schemas/foundation/test_reconciliation.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/api/schemas/foundation/test_trust.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/api/test_exception_registry.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/api/test_openapi_compat_actions.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/api/test_openapi_compat_perf_budget.py | requires assertion-level review | [88] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/api/ws/test_market_ws_dispatch.py | requires assertion-level review | [105] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/config/test_profile.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/event_bus/test_policy.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/indicators/test_dsl_indicator.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/indicators/test_indicator_on_indicator.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/indicators/test_ml_catalog.py | requires assertion-level review | [206, 241] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/indicators/test_oss_eval_gate_deepen_2919.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/indicators/test_pandas_ta_classic_dependency_ind11.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/indicators/test_reference_sampling.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/indicators/test_registry_tiers.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/loader/test_secret_loader.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/notifications/test_preferences.py | requires assertion-level review | [205, 226] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/observability/test_loop_health.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/observability/test_metrics_port.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/portfolio/sizing/test_fixed_fractional.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/portfolio/sizing/test_selector.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/portfolio/test_accounting.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/portfolio/test_adversarial_binding.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/portfolio/test_engine_v2.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/portfolio/test_mandate_binding.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/portfolio/test_portfolio_contracts.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/portfolio/test_rebalance.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/script/import_/test_pine_parser.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/script/test_ast.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/script/test_checker.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/script/test_compile.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/script/test_cond_v2_bridge.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/script/test_interpreter_property.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/script/test_lexer.py | requires assertion-level review | [306] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/script/test_lookahead.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/script/test_pine_transpile.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/script/test_request_future_reference.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/script/test_request_mtf.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/script/test_request_node.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/script/test_resources.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/script/test_script_library.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/strategy/test_confidence.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/strategy/test_indicator_key.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/strategy/test_market_state.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/strategy/test_models.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/strategy/test_script_facade.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/strategy/test_state_memory.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/test_bitemporal.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/test_data_distrust_monitor.py | requires assertion-level review | [172] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/test_engine_v2.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/test_event_bus_trace.py | requires assertion-level review | [257] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/test_portfolio_engine.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/test_split_brain.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/core/validator/test_result.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/data/models/test_memory.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/data/models/test_serialization.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/data/models/test_strategy_fsm.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/ems/test_pov.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/executor/test_executor_client_order_id_deepen.py | requires assertion-level review | [256] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/backtest/test_deep_backtest_job.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/backtest/test_fill_simulator.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/backtest/test_vector_oss_eval_gate_deepen_3048.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/backtest/vector/test_vector_sweeps.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/backtest/vector/test_vector_universe.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/ems/test_decomposition.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/ems/test_get_algo_progress.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/market_data/adapters/ingest/test_l2_ingest_session_deepen_2902.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/market_data/test_realtime_fanout.py | requires assertion-level review | [348] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/market_data/test_tick_parquet.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/marketplace/test_contracts_v1.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/marketplace/test_source_retention.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/marketplace/test_visibility.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/reconciliation/test_projections.py | requires assertion-level review | [137] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/research_data/test_entity_link_deepen_2906.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/research_data/test_gdelt_source.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/research_data/test_ingest_job.py | requires assertion-level review | [461] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/research_data/test_link_entities.py | requires assertion-level review | [299] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/research_data/test_link_entities_deepen_2906.py | requires assertion-level review | [180] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/research_data/test_macro_series.py | requires assertion-level review | [256, 292] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/research_data/test_rss_generic_source.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/research_data/test_source_eval_gate_deepen_2904.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/tax_report/test_capital_gains.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/whatif/application/test_preview_order.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/foundation/whatif/domain/test_impact.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/market_data/test_adjustment.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/market_data/test_contracts_schema.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/market_data/test_dedupe.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/market_data/test_gap_detector.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/market_data/test_kis_ingest_source.py | requires assertion-level review | [263] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/market_data/test_lifecycle.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/market_data/test_lineage.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/market_data/test_ohlc_sanity.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/market_data/test_options_chain.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/market_data/test_outlier_detector.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/market_data/test_session_rules.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/market_data/test_stale_detector.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/market_data/test_symbol_normalizer.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/market_data/test_timeframe.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/market_data/test_verdict.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/market_data/test_yaml_calendar_source_deepen_2901.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/ops/test_backup_verify.py | requires assertion-level review | [292, 317] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/research_data/test_ingest_opendart_filing.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/research_data/test_opendart_rd20_deepen_2903.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/research_data/test_point_in_time.py | requires assertion-level review | [216] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/research_data/test_query_as_of.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/scripts/test_canary_bitget.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/scripts/test_check_audit_regressions.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/scripts/test_check_complexity.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/scripts/test_check_idempotent_writes.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/scripts/test_check_import_linter.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/scripts/test_check_zone_diff.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/scripts/test_kis_tr_fetch.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/scripts/test_plt42_env_example_audit.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/scripts/test_pyproject_importlinter_dom1.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/scripts/test_replay_verify_db_pressure.py | requires assertion-level review | [59] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/scripts/test_replay_verify_pool_retry.py | requires assertion-level review | [312] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/scripts/test_run_mutation.py | requires assertion-level review | [] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/scripts/test_setup_test_db.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/services/test_candle_history.py | requires assertion-level review | [297] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/services/test_correlation_service.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/services/test_market_state_multi_tf.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/services/test_reference_quotes.py | requires assertion-level review | [236] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/services/test_seller_suspension_service.py | requires assertion-level review | [220] | 미실측 | 이름 존재·실행 미확인 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/services/test_var_estimator.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/services/test_verification_queue_service.py | requires assertion-level review | [193] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/test_gate_params_required.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |
| tests/unit/test_zone_purity.py | requires assertion-level review | [] | 미실측 | 증거 없음 | JSON diff·판정 미확정 | 후속 검증 |

## 이번 리프 검증 및 제한

- `-n 8` 세 차례: 각각 13 passed (44.85s, 26.16s, 38.82s). 일반 명령의 워커 상한 예외는 이 TASK의 명시적 DoD에 한정한다.
- 50ms 락 지연을 같은 실제 테스트에 주입: p99=62.670225ms, 예산=5ms, batch=8, AssertionError/exit=1.
- 헬퍼/감사 도구 음성 테스트 8 passed; ruff, mypy(1569 files), zone, ratchets, perf marker 녹색.
- 최초 mypy의 NUL 캐시 옵션은 내부 오류로 실패했고 정상 `--no-incremental`로 재검증했다.
- 측정치는 배치 평균의 p99다. 개별 호출 꼬리 지연의 p99 인증은 아니며, 이 제한을 숨기지 않는다.
- 기존 CPU 정상 검사와 wall 지연 검사 불일치를 제거했다. 대기 시간도 지연 예산에 포함한다.
- 나머지 313개 파일은 동적 검출력/예산 불변 미확정이다. 정적 의심을 확정 결함이나 검증 완료로 보고하지 않는다.
- 실제 분할 발행 결과는 `task-11539-split-results.json`에 기록한다. PM 소유 명세는 직접 편집하지 않았다.
- 이번 범위는 테스트/감사 도구로 제품·주문 경로 변경 없음. D3 replay_verify는 N/A(실행/원장 상태 변경 없음).
- 지정 보존 패치와 HEAD는 task-11563의 CODEOWNERS 변경이었다. 원래 `wt/codex-escalation-1`에 보존하고 별도 브랜치에서 작업했다.
