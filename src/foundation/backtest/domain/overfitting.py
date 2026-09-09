"""L34 -- `backtest/domain/overfitting.py`: Deflated Sharpe Ratio and PBO(CSCV) pure
calculation.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md #9 L34 (formulas #3.5 lines
374-375; public contract #2 table `overfitting.py` row). Pure domain module -- no I/O,
no randomness, stdlib only (`math`, `statistics`, `decimal`).

formulas transcribed from spec §3.5; Bailey & Lopez de Prado originals not yet
cross-checked (spec §10 U3) -- keep ofit-v1, bump to v2 on any correction.
"""
from __future__ import annotations

import math
from decimal import Decimal
from itertools import combinations
from statistics import NormalDist

__all__ = ["OVERFITTING_VERSION", "OverfittingError", "deflated_sharpe", "pbo_cscv"]

OVERFITTING_VERSION = "ofit-v1"

_EULER_MASCHERONI = 0.5772
_NORMAL = NormalDist()


class OverfittingError(ValueError):
    """Fail-closed rejection for `overfitting.py` -- degenerate inputs that would
    otherwise force a 0/1/None/NaN placeholder value (spec #3.5: "substituting 0 is forbidden")."""


def deflated_sharpe(
    sr_hat: Decimal,
    n_trials: int,
    T: int,
    skew: Decimal,
    kurt: Decimal,
    sr_var: Decimal,
) -> Decimal:
    """`DSR = Phi((SR_hat - SR0) * sqrt(T-1) / sqrt(1 - skew*SR_hat + ((kurt-1)/4)*SR_hat**2))`,
    `SR0 = sqrt(sr_var) * ((1-gamma)*Phi^-1(1-1/N) + gamma*Phi^-1(1-1/(N*e)))`,
    `gamma = 0.5772` (Euler-Mascheroni). `N = n_trials`."""
    if n_trials < 2:
        raise OverfittingError(f"n_trials must be >= 2, got {n_trials}")
    if T < 2:
        raise OverfittingError(f"T must be >= 2, got {T}")
    if sr_var <= 0:
        raise OverfittingError(f"sr_var must be > 0, got {sr_var}")

    sr_hat_f = float(sr_hat)
    skew_f = float(skew)
    kurt_f = float(kurt)
    sr0 = math.sqrt(float(sr_var)) * (
        (1 - _EULER_MASCHERONI) * _NORMAL.inv_cdf(1 - 1 / n_trials)
        + _EULER_MASCHERONI * _NORMAL.inv_cdf(1 - 1 / (n_trials * math.e))
    )
    numerator = (sr_hat_f - sr0) * math.sqrt(T - 1)
    denominator = math.sqrt(1 - skew_f * sr_hat_f + ((kurt_f - 1) / 4) * sr_hat_f**2)
    dsr = _NORMAL.cdf(numerator / denominator)
    return Decimal(repr(dsr))


def pbo_cscv(perf_matrix: list[list[Decimal]], n_blocks: int) -> Decimal:
    """Combinatorially Symmetric Cross-Validation. Splits the `T`-row performance
    matrix into `n_blocks` contiguous blocks, and for each `C(S, S/2)` deterministic
    split (`itertools.combinations`) of blocks into an IS half and an OOS half, finds
    the column with the best mean IS performance and ranks its mean OOS performance
    among all `N` columns. `omega = rank / (N+1)` (open interval, rank 1 = worst OOS,
    N = best OOS); `lambda = ln(omega / (1-omega))`; `PBO = #(lambda<0) / #combinations`."""
    if not perf_matrix or not perf_matrix[0]:
        raise OverfittingError("perf_matrix must be non-empty")
    row_length = len(perf_matrix[0])
    if any(len(row) != row_length for row in perf_matrix):
        raise OverfittingError("perf_matrix rows must all have the same length (ragged)")
    n_rows = len(perf_matrix)
    n_cols = row_length
    if n_blocks < 2 or n_blocks % 2 != 0 or n_blocks > n_rows:
        raise OverfittingError(
            f"n_blocks must be even, >= 2, and <= T ({n_rows}), got {n_blocks}"
        )

    blocks = _split_blocks(n_rows, n_blocks)
    below_zero = 0
    total = 0
    for is_block_indices in combinations(range(n_blocks), n_blocks // 2):
        is_rows = [row for block in is_block_indices for row in blocks[block]]
        oos_rows = [
            row
            for block in range(n_blocks)
            if block not in is_block_indices
            for row in blocks[block]
        ]

        is_means = [_column_mean(perf_matrix, is_rows, col) for col in range(n_cols)]
        oos_means = [_column_mean(perf_matrix, oos_rows, col) for col in range(n_cols)]

        best_col = max(range(n_cols), key=lambda col: is_means[col])
        oos_rank_order = sorted(range(n_cols), key=lambda col: (oos_means[col], col))
        rank = oos_rank_order.index(best_col) + 1
        omega = Decimal(rank) / Decimal(n_cols + 1)
        lam = (omega / (1 - omega)).ln()
        if lam < 0:
            below_zero += 1
        total += 1

    return Decimal(below_zero) / Decimal(total)


def _split_blocks(n_rows: int, n_blocks: int) -> list[list[int]]:
    """Divides `range(n_rows)` into `n_blocks` contiguous, near-equal-size, gap-free
    blocks (earlier blocks absorb the remainder), preserving row order."""
    size, remainder = divmod(n_rows, n_blocks)
    blocks: list[list[int]] = []
    start = 0
    for block_index in range(n_blocks):
        this_size = size + (1 if block_index < remainder else 0)
        blocks.append(list(range(start, start + this_size)))
        start += this_size
    return blocks


def _column_mean(matrix: list[list[Decimal]], rows: list[int], col: int) -> Decimal:
    values = [matrix[row][col] for row in rows]
    return sum(values, Decimal(0)) / len(values)
