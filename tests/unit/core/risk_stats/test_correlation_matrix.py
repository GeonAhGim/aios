"""L4_risk_and_safety_v1.0.md#R-20 — correlation_matrix.py 대칭성·None·known-value."""
import numpy as np
import pytest

from src.core.risk_stats.correlation_matrix import pearson_matrix


def test_perfectly_correlated_pair_is_one():
    R = {"A": [1.0, 2.0, 3.0, 4.0, 5.0], "B": [2.0, 4.0, 6.0, 8.0, 10.0]}
    result = pearson_matrix(R, min_overlap=3)
    assert result[("A", "B")] == pytest.approx(1.0)


def test_perfectly_anti_correlated_pair_is_minus_one():
    R = {"A": [1.0, 2.0, 3.0, 4.0, 5.0], "B": [-1.0, -2.0, -3.0, -4.0, -5.0]}
    result = pearson_matrix(R, min_overlap=3)
    assert result[("A", "B")] == pytest.approx(-1.0)


def test_matrix_is_symmetric():
    R = {"A": [1.0, 2.0, -1.0, 3.0, 0.5], "B": [0.5, -1.0, 2.0, 1.0, 3.0]}
    result = pearson_matrix(R, min_overlap=3)
    assert result[("A", "B")] == result[("B", "A")]


def test_insufficient_overlap_is_none():
    R = {
        "A": [1.0, 2.0, 3.0, 4.0, 5.0],
        "B": [np.nan, np.nan, np.nan, 4.0, 5.0],
    }
    result = pearson_matrix(R, min_overlap=3)
    assert result[("A", "B")] is None
    assert result[("B", "A")] is None


def test_constant_series_correlation_is_none():
    R = {"A": [1.0, 2.0, 3.0, 4.0, 5.0], "B": [1.0, 1.0, 1.0, 1.0, 1.0]}
    result = pearson_matrix(R, min_overlap=3)
    assert result[("A", "B")] is None


def test_diagonal_self_correlation_is_one():
    R = {"A": [1.0, 2.0, 3.0, -1.0, 0.5]}
    result = pearson_matrix(R, min_overlap=3)
    assert result[("A", "A")] == pytest.approx(1.0)


def test_ewma_lambda_weights_recent_observations_more():
    R = {"A": [-1.0, -1.0, -1.0, 1.0, 1.0], "B": [1.0, 1.0, 1.0, 1.0, -1.0]}
    equal = pearson_matrix(R, min_overlap=3)[("A", "B")]
    ewma = pearson_matrix(R, min_overlap=3, ewma_lambda=0.5)[("A", "B")]
    assert equal is not None and ewma is not None
    assert equal != ewma
