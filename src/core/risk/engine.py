"""FD-8.3 — 리스크 검사 (RiskEngine, 가장 중요).

Spec: 기능설계문서_v1.21.md#FD-8.3, 03_core_modules_v1.1.md#§3.7, config/risk_policy.yaml

8.2-A Master Authority의 핵심 구현체 — 이 클래스는 어떤 LLM/Agent
판단도 거치지 않는다. `risk_policy.yaml`의 값과 이미 계산된
`account_state` 딕셔너리에 대한 단순 임계치 비교만 수행한다 — 상관계수
집계·VaR 추정 같은 통계 계산 자체는 이 클래스의 책임이 아니다(별도
조립자가 `account_state`를 채워 넘긴다). 이렇게 나눠야 이 클래스가
"가장 신중하게 구현되어야 하는 부분"이라는 요구를 실제로 만족할 만큼
단순하고 감사 가능하게 유지된다.

이 함수를 통과하지 못한 Allocation은 어떤 경로로도 Executor에 도달할 수
없다 — 이 프로젝트에서 가장 중요한 단일 불변식이다.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.portfolio.models import AllocationDecision
from src.core.risk.models import RiskCheckResult
from src.services.capital_allocation import allocation_cap_pct

_UNSAFE_CIRCUIT_BREAKER_LEVELS = frozenset({"restricted", "halted", "emergency"})


class RiskEngine:
    def __init__(self, policy: RiskPolicy) -> None:
        self._policy = policy

    def check(
        self, allocation: AllocationDecision, account_state: dict[str, Any]
    ) -> RiskCheckResult:
        """`account_state` 필수 키(전부 Draft 근사치 — 8.2-B 원문 그대로):

        - `daily_pnl_pct: Decimal | None` — 오늘 실현+미실현 손익률(음수=손실)
        - `drawdown_pct: Decimal | None` — 계좌 peak 대비 현재 낙폭(양수)
        - `position_quantity: Decimal | None` — 이 심볼의 기존 보유 수량
          (0=무포지션=신규진입 — 집중도 검사는 진입에만 적용, 청산은 항상
          노출을 줄이므로 통과)
        - `total_equity: Decimal | None`
        - `certified_badge: bool | None`, `allocated_capital: Decimal | None`,
          `available_balance: Decimal | None` — FD-16.1 재검증용
        - `var_pct: Decimal | None` — 최근 20틱 수익률 표준편차 기반 근사(Draft)
        - `correlated_exposure_pct: Decimal | None` — 상관계수 0.7 초과 심볼들의
          합산 노출률(Draft 사전계산 테이블 기반)
        - `recent_trade_count_1h: int | None`, `avg_trade_count_24h: float | None`
        - `circuit_breaker_level: str | None`, `execution_paused_by_safety: bool | None`

        각 지표는 필요한 값이 하나라도 없으면 "판단 불가"로 즉시 거부한다
        (판단 불가를 승인으로 취급하지 않는다 — Master Authority 핵심 원칙).
        """
        checked: list[str] = []
        policy = self._policy

        checked.append("daily_loss")
        daily_pnl_pct = account_state.get("daily_pnl_pct")
        if daily_pnl_pct is None:
            return RiskCheckResult(
                approved=False,
                rejection_reason="daily_loss_data_unavailable",
                checked_rules=checked.copy(),
            )
        if daily_pnl_pct <= -Decimal(str(policy.daily_loss.halt_pct)):
            return RiskCheckResult(
                approved=False,
                rejection_reason="daily_loss_halt_exceeded",
                checked_rules=checked.copy(),
            )

        checked.append("max_drawdown")
        drawdown_pct = account_state.get("drawdown_pct")
        if drawdown_pct is None:
            return RiskCheckResult(
                approved=False,
                rejection_reason="max_drawdown_data_unavailable",
                checked_rules=checked.copy(),
            )
        if drawdown_pct >= Decimal(str(policy.max_drawdown.hard_stop_pct)):
            return RiskCheckResult(
                approved=False,
                rejection_reason="max_drawdown_hard_stop_exceeded",
                checked_rules=checked.copy(),
            )

        # Leverage — 사용자 승인(2026-09-02) FROZEN 존 수정. Phase 1은 크립토
        # 현물 전용이라 지금은 account_state["leverage"]가 항상 1.0으로
        # 들어온다(positions.leverage 스키마 기본값, account_state.py 참조).
        # coverage_multiplier(참조데이터 커버리지 등급별 조정, risk_policy.yaml)는
        # 그 등급을 계산해 넣는 입력 경로가 아직 없어 default_max만 비교한다
        # — 파생상품 확장 시 이 필드가 채워지면 이 비교식은 그대로 재사용된다.
        checked.append("leverage")
        leverage = account_state.get("leverage")
        if leverage is None:
            return RiskCheckResult(
                approved=False,
                rejection_reason="leverage_data_unavailable",
                checked_rules=checked.copy(),
            )
        if leverage > Decimal(str(policy.leverage.default_max)):
            return RiskCheckResult(
                approved=False,
                rejection_reason="leverage_exceeded",
                checked_rules=checked.copy(),
            )

        checked.append("position_concentration")
        total_equity = account_state.get("total_equity")
        position_quantity = account_state.get("position_quantity")
        if total_equity is None or total_equity <= 0 or position_quantity is None:
            return RiskCheckResult(
                approved=False,
                rejection_reason="position_concentration_data_unavailable",
                checked_rules=checked.copy(),
            )
        if position_quantity == 0 and allocation.capital_pct > Decimal(
            str(policy.position_concentration.single_asset_max_pct)
        ):
            return RiskCheckResult(
                approved=False,
                rejection_reason="position_concentration_exceeded",
                checked_rules=checked.copy(),
            )

        checked.append("strategy_allocation")
        certified_badge = account_state.get("certified_badge")
        allocated_capital = account_state.get("allocated_capital")
        available_balance = account_state.get("available_balance")
        if (
            certified_badge is None
            or allocated_capital is None
            or available_balance is None
            or available_balance <= 0
        ):
            return RiskCheckResult(
                approved=False,
                rejection_reason="strategy_allocation_data_unavailable",
                checked_rules=checked.copy(),
            )
        cap_pct = allocation_cap_pct(certified_badge, policy.strategy_allocation)
        requested_pct = (allocated_capital / available_balance) * Decimal("100")
        if requested_pct > cap_pct:
            return RiskCheckResult(
                approved=False,
                rejection_reason="strategy_allocation_exceeded",
                checked_rules=checked.copy(),
            )

        checked.append("var")
        var_pct = account_state.get("var_pct")
        if var_pct is None:
            return RiskCheckResult(
                approved=False,
                rejection_reason="var_data_unavailable",
                checked_rules=checked.copy(),
            )
        if var_pct > Decimal(str(policy.var.max_pct)):
            return RiskCheckResult(
                approved=False, rejection_reason="var_exceeded", checked_rules=checked.copy()
            )

        checked.append("correlation_risk")
        correlated_exposure_pct = account_state.get("correlated_exposure_pct")
        if correlated_exposure_pct is None:
            return RiskCheckResult(
                approved=False,
                rejection_reason="correlation_risk_data_unavailable",
                checked_rules=checked.copy(),
            )
        correlation_risk_limit = Decimal(str(policy.correlation_risk.aggregate_exposure_max_pct))
        if correlated_exposure_pct > correlation_risk_limit:
            return RiskCheckResult(
                approved=False,
                rejection_reason="correlation_risk_exceeded",
                checked_rules=checked.copy(),
            )

        checked.append("trade_frequency")
        recent_trade_count_1h = account_state.get("recent_trade_count_1h")
        avg_trade_count_24h = account_state.get("avg_trade_count_24h")
        if recent_trade_count_1h is None or avg_trade_count_24h is None:
            return RiskCheckResult(
                approved=False,
                rejection_reason="trade_frequency_data_unavailable",
                checked_rules=checked.copy(),
            )
        # 24시간 평균이 0이면 비교 기준 자체가 없다(첫 거래들을 이상거래로
        # 오판하지 않는다) — Draft 한계, 충분한 이력이 쌓이면 자연히 정상 동작.
        anomaly_threshold = avg_trade_count_24h * policy.trade_frequency.anomaly_multiplier
        if avg_trade_count_24h > 0 and recent_trade_count_1h > anomaly_threshold:
            return RiskCheckResult(
                approved=False,
                rejection_reason="trade_frequency_anomaly",
                checked_rules=checked.copy(),
            )

        checked.append("safety_state")
        circuit_breaker_level = account_state.get("circuit_breaker_level")
        execution_paused_by_safety = account_state.get("execution_paused_by_safety")
        if circuit_breaker_level is None or execution_paused_by_safety is None:
            return RiskCheckResult(
                approved=False,
                rejection_reason="safety_state_data_unavailable",
                checked_rules=checked.copy(),
            )
        if circuit_breaker_level in _UNSAFE_CIRCUIT_BREAKER_LEVELS or execution_paused_by_safety:
            return RiskCheckResult(
                approved=False,
                rejection_reason="safety_state_blocked",
                checked_rules=checked.copy(),
            )

        return RiskCheckResult(approved=True, rejection_reason=None, checked_rules=checked.copy())
