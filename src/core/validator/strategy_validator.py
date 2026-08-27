"""5.8 — Validator.validate_strategy_config().

Spec: 03_core_modules_v1.1.md#§3.3, 08_test_plan_v1.2.md#§8.2
("FSM 무결성 위반 케이스 5종 이상: 고아 state, 자기순환 등")

9.11 FSM 구조 자체의 무결성만 검증한다 — 조건식(condition 문자열) 자체의
평가는 FROZEN Zone(Strategy Engine)의 책임(03번 §3.9 Zone 경계).
"""
from __future__ import annotations

from src.core.validator.result import ValidationResult
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig


def validate_strategy_config(config: FSMStrategyConfig) -> ValidationResult:
    errors: list[str] = []
    declared_states = set(config.states)

    if config.initial_state not in declared_states:
        errors.append(f"initial_state({config.initial_state.value})가 states 목록에 없습니다.")

    referenced_states: set[FSMState] = set()
    seen_transitions: set[tuple[FSMState, FSMState, str]] = set()
    for t in config.transitions:
        referenced_states.add(t.from_state)
        referenced_states.add(t.to_state)

        if t.from_state not in declared_states:
            errors.append(f"transition.from_state({t.from_state.value})가 states 목록에 없습니다.")
        if t.to_state not in declared_states:
            errors.append(f"transition.to_state({t.to_state.value})가 states 목록에 없습니다.")

        if t.from_state == t.to_state:
            errors.append(f"자기순환 transition 발견: {t.from_state.value} -> {t.to_state.value}")

        key = (t.from_state, t.to_state, t.condition)
        if key in seen_transitions:
            errors.append(
                f"중복 transition 발견: {t.from_state.value} -> {t.to_state.value} "
                f"({t.condition})"
            )
        seen_transitions.add(key)

    orphan_states = declared_states - referenced_states
    for state in orphan_states:
        errors.append(f"고아 state 발견(어떤 transition에도 참여하지 않음): {state.value}")

    return ValidationResult(is_valid=not errors, errors=errors)
