"""확장 어댑터 메서드(주문성 API)용 공용 LIVE 모드 가드.

Executor.execute()는 두 독립 검사로 실주문을 막는다: (1) `mode !=
"PAPER"` 하드 차단, (2) `adapter.is_paper_trading`/`is_sandboxed` 확인.
이 중 (1)은 실행 레코드(strategy_executions.mode)라는 호출자 컨텍스트에
있어 adapter 인스턴스 스스로는 알 수 없다 — 그래서 이 데코레이터가
대신할 수 있는 건 (2)뿐이다.

레드팀 #2026-09-02-32 — Convert/Grid/Strategy/Margin/Futures/Loan/
Subaccount 확장 메서드는 `Executor`를 거치지 않고 거래소에 직결되어
이 방어선이 전혀 없었다. 이 데코레이터를 적용하면 최소한 "LIVE로
구성된(demo_mode=False) adapter는 이 메서드를 아예 실행할 수 없다"는
방어선은 확보된다 — Executor가 주는 이중 방어의 완전한 대체는 아니지만,
다음 leaf가 이 메서드들을 라우터에 배선하면서 그 가드를 재발명하는 걸
잊어도 최소 안전장치는 남는다.
"""
from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from src.core.exceptions import FrozenZonePaperAdapterBlockedError

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def require_paper_sandbox(func: F) -> F:
    @functools.wraps(func)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if not (self.is_paper_trading and self.is_sandboxed):
            raise FrozenZonePaperAdapterBlockedError(
                f"{func.__qualname__}은(는) PAPER/sandbox로 구성된 adapter에서만 "
                "호출할 수 있습니다(레드팀 #2026-09-02-32) — LIVE로 구성된 "
                "adapter에서는 Executor를 거치지 않는 이 확장 메서드가 차단됩니다."
            )
        return await func(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]
