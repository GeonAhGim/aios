"""eventstore 통합테스트 패키지 — 부정입·실패주입·성능예산 증빙 위치.

이 파일은 빈 패키지 마커(`__init__.py`)로 pytest가 테스트를 수집하지 않는다.
모든 negative / 실패주입 / gate-red / 성능단언 증빙은 sibling 파일에 둔다.

증빙 매핑:
- negative test 3건: ``test_conftest_guards.py``
  - ``test_unregistered_replay_module_is_rejected`` — 미등록 replay 모듈 ValueError 거부
  - ``test_ledger_control_rejects_second_singleton_row`` — CHECK (id=1) 위반 거부
  - ``test_ledger_control_unfrozen_by_rejects_unknown_user`` — FK(users.user_id) 위반 거부
- 실패주입 1건: ``test_conftest_guards.py::test_missing_database_url_propagates_keyerror``
  - DATABASE_URL 부재가 KeyError로 전파됨 확인
- gate-red 재현: ``test_replay_verify.py``
  - ``test_replay_deterministic_gate_red_with_delayed_fills``
  - ``test_replay_verify_gate_red_reproduction_with_delayed_fills``
- 성능단언: ``test_replay_verify.py`` (RelativeBudget p95/p99 단언)
- 불변식 검증: ``test_projections*.py``, ``test_replay_verify_tamper_detection.py``

DoD(D2): negative≥3 ✓, 실패주입≥1 ✓, gate-red≥1 ✓, 성능단언≥1 ✓
"""
