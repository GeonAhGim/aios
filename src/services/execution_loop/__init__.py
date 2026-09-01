"""FD-8.1~8.4 실행 루프 배선 — src/services/execution_loop/.

StrategyEngine/PortfolioEngine/RiskEngine/Executor(src/core/) 자체는
순수 판단 로직이다. 이 패키지는 그 4개를 실제 DB/거래소 상태와 연결하는
오케스트레이션 계층으로, FD-8 세부기능 번호를 자체적으로 갖지 않는다.
"""
from __future__ import annotations

from src.services.execution_loop.tick import run_execution_tick

__all__ = ["run_execution_tick"]
