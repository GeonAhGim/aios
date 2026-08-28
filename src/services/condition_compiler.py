"""14.2(백엔드 부분)/14.5 — 조건 조합 → FSM 컴파일 (ConditionCompiler).

Spec: 기능설계문서_v1.20.md#FD-14.2 처리단계 1/3/4, 9.11(FSMStrategyConfig), 06번 §6.1/6.2

FD-14.2는 "조건 조합 UI"(프론트엔드)와 "FSM 컴파일"(순수 로직) 두 갈래로
나뉜다 — 이 세션은 backend 전용이라 UI는 스콥 밖이지만, 컴파일 로직
자체는 UI 없이도 완전히 구현·테스트 가능한 순수 함수라 여기서 채운다
(16_backend_signatures.md가 이미 ConditionCompiler를 별도 클래스로
예정해뒀다).

사용자에게는 "진입/청산/손절 조건" 3개 입력으로 단순화하고, 내부적으로
FSM 6개 상태·전이로 자동 변환한다:

    IDLE --entry--> BUY_ORDER_PENDING --ORDER_FILLED--> HOLDING
    HOLDING --exit--> SELL_ORDER_PENDING --ORDER_FILLED--> IDLE
    HOLDING --stop_loss--> STOP_LOSS --ORDER_FILLED--> IDLE

ORDER_FILLED는 사용자가 만드는 조건이 아니라 주문 체결이라는 시스템
이벤트를 나타내는 예약 리터럴이다 — 그 의미 해석은 FROZEN Strategy
Engine(FD-4/FD-8) 몫이고 이 컴파일러는 문자열만 배치한다. EMERGENCY_EXIT는
이 컴파일러가 생성하지 않는다 — Watchdog 등 안전장치가 외부에서 강제
전이시키는 상태이지 사용자 조건 조합의 대상이 아니다.

이 고정된 6상태/6전이 구조 자체가 모순을 만들 수 없는 형태라, FD-14.2가
말하는 "모순되는 상태 전이로 컴파일 불가"는 (임의 그래프를 만드는 범용
조건빌더가 아닌) 이 단순 컴파일러에서는 "필수 조건 그룹이 비어있음"으로
좁혀 처리한다.
"""
from __future__ import annotations

from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.services.preview_service import PreviewCondition

ORDER_FILLED = "ORDER_FILLED"

# 06번 §6.2 Draft — 최종 리스트는 실제 착수 시 확정 예정이지만, 지금은
# 이 5개를 시스템 공통 화이트리스트로 강제한다(사용자별 상이한 심볼셋은
# Phase 1 스콥 아님, §6.1).
TARGET_ASSET_WHITELIST = frozenset({"BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT"})

_OPERATOR_SYMBOLS = {
    ">": ">",
    "<": "<",
    ">=": ">=",
    "<=": "<=",
    "==": "==",
    "crosses_above": "CROSSES_ABOVE",
    "crosses_below": "CROSSES_BELOW",
}


class ConditionCompileError(Exception):
    """FD-14.2 예외상황 — 컴파일 불가 또는 화이트리스트 밖 자산. 라우터가 400으로 변환."""


def _compile_condition_group(conditions: list[PreviewCondition], combine: str) -> str:
    if not conditions:
        raise ConditionCompileError("조건이 최소 1개 이상 필요합니다.")
    if combine not in ("AND", "OR"):
        raise ConditionCompileError(f"지원하지 않는 결합 방식입니다: {combine}")

    parts = []
    for condition in conditions:
        if condition.operator not in _OPERATOR_SYMBOLS:
            raise ConditionCompileError(f"지원하지 않는 연산자입니다: {condition.operator}")
        params_suffix = "".join(
            f"_{key}{value}" for key, value in sorted(condition.params.items())
        )
        symbol = _OPERATOR_SYMBOLS[condition.operator]
        parts.append(f"{condition.indicator}{params_suffix} {symbol} {condition.threshold}")

    joiner = " AND " if combine == "AND" else " OR "
    return joiner.join(parts)


class ConditionCompiler:
    def compile(
        self,
        *,
        strategy_id: str,
        version: str,
        target_asset: str,
        market: str,
        exchange: str,
        author_agent: str,
        entry_conditions: list[PreviewCondition],
        exit_conditions: list[PreviewCondition],
        stop_loss_conditions: list[PreviewCondition],
        entry_combine: str = "AND",
        exit_combine: str = "AND",
        stop_loss_combine: str = "AND",
    ) -> FSMStrategyConfig:
        if target_asset not in TARGET_ASSET_WHITELIST:
            raise ConditionCompileError(
                f"화이트리스트에 없는 target_asset입니다: {target_asset}"
            )

        entry_expr = _compile_condition_group(entry_conditions, entry_combine)
        exit_expr = _compile_condition_group(exit_conditions, exit_combine)
        stop_loss_expr = _compile_condition_group(stop_loss_conditions, stop_loss_combine)

        return FSMStrategyConfig(
            strategy_id=strategy_id,
            version=version,
            target_asset=target_asset,
            market=market,
            exchange=exchange,
            initial_state=FSMState.IDLE,
            states=[
                FSMState.IDLE,
                FSMState.BUY_ORDER_PENDING,
                FSMState.HOLDING,
                FSMState.SELL_ORDER_PENDING,
                FSMState.STOP_LOSS,
                FSMState.EMERGENCY_EXIT,
            ],
            transitions=[
                FSMTransition(
                    from_state=FSMState.IDLE,
                    to_state=FSMState.BUY_ORDER_PENDING,
                    condition=entry_expr,
                ),
                FSMTransition(
                    from_state=FSMState.BUY_ORDER_PENDING,
                    to_state=FSMState.HOLDING,
                    condition=ORDER_FILLED,
                ),
                FSMTransition(
                    from_state=FSMState.HOLDING,
                    to_state=FSMState.SELL_ORDER_PENDING,
                    condition=exit_expr,
                ),
                FSMTransition(
                    from_state=FSMState.HOLDING,
                    to_state=FSMState.STOP_LOSS,
                    condition=stop_loss_expr,
                ),
                FSMTransition(
                    from_state=FSMState.SELL_ORDER_PENDING,
                    to_state=FSMState.IDLE,
                    condition=ORDER_FILLED,
                ),
                FSMTransition(
                    from_state=FSMState.STOP_LOSS,
                    to_state=FSMState.IDLE,
                    condition=ORDER_FILLED,
                ),
            ],
            author_agent=author_agent,
        )
