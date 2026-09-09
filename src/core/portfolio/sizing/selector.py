"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 94 — sizing method dispatch.

방법 → 함수 디스패치 + 결과 `inputs_hash` 검증. `config.method`와
`inp.portfolio_config.method`가 어긋나면(호출자 버그) 즉시 거부하고, 반환된
`SizingResult.method`가 요청한 방법과 다르거나 `inputs_hash`가 sha256 hex
형태가 아니면(구현 오류·위조) 마찬가지로 거부한다.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from src.core.portfolio.config import PortfolioConfig, SizingMethod
from src.core.portfolio.sizing import (
    PortfolioSizingError,
    SizingResult,
    fixed_fractional,
    kelly_capped,
    risk_parity,
    volatility_target,
)
from src.core.portfolio.state_input import PortfolioStateInput

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

_DISPATCH: dict[SizingMethod, Callable[[PortfolioStateInput], SizingResult]] = {
    SizingMethod.FIXED_FRACTIONAL: fixed_fractional.size,
    SizingMethod.VOLATILITY_TARGET: volatility_target.size,
    SizingMethod.KELLY_CAPPED: kelly_capped.size,
    SizingMethod.RISK_PARITY: risk_parity.size,
}


class UnknownSizingMethodError(PortfolioSizingError):
    code = "PORTFOLIO_SIZING_METHOD_UNKNOWN"


class SizingResultTamperedError(PortfolioSizingError):
    code = "PORTFOLIO_SIZING_RESULT_TAMPERED"


def size_for(config: PortfolioConfig, inp: PortfolioStateInput) -> SizingResult:
    if config.method != inp.portfolio_config.method:
        raise SizingResultTamperedError(
            f"{SizingResultTamperedError.code}: config.method={config.method} != "
            f"inp.portfolio_config.method={inp.portfolio_config.method}"
        )

    sizing_fn = _DISPATCH.get(config.method)
    if sizing_fn is None:
        raise UnknownSizingMethodError(f"{UnknownSizingMethodError.code}: {config.method}")

    result = sizing_fn(inp)

    if result.method != config.method:
        raise SizingResultTamperedError(
            f"{SizingResultTamperedError.code}: result.method={result.method} != "
            f"config.method={config.method}"
        )
    if not _SHA256_HEX_RE.fullmatch(result.inputs_hash):
        raise SizingResultTamperedError(
            f"{SizingResultTamperedError.code}: inputs_hash is not a sha256 hex digest"
        )
    return result
