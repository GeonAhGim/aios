"""build_tearsheet() 단위테스트 — BT-12.

지표 값 자체(Sharpe 산식 등)는 test_compute_metrics.py가 이미 검증한다.
여기서는 "compute_metrics()가 낸 BacktestMetrics를 리포트 뷰로 그대로
옮기는가", "결정론(같은 입력=같은 출력)", "빈 equity_curve 거부"만 본다.

DEEPEN(task-3054): negative 1건(빈 equity_curve)만 있어 D2 하한(negative
≥3) 미달이었다. 새 기능 추가 없이 이 리프의 증빙만 보강한다 — negative
2건(None equity_curve, 계약 위반 basis) 추가, 수치 성능 단언·실패 주입·
게이트 적색 재현 3종 신설.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.foundation.backtest.application.compute_metrics import compute_metrics
from src.foundation.backtest.application.tearsheet import build_tearsheet
from src.foundation.backtest.domain.models import (
    BacktestConfig,
    BacktestResult,
    CostModel,
    EquityPoint,
)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _point(i: int, equity: str, drawdown: str = "0") -> EquityPoint:
    return EquityPoint(
        bar_index=i,
        timestamp=_T0 + timedelta(hours=i),
        equity=Decimal(equity),
        drawdown_pct=Decimal(drawdown),
    )


def _config(**overrides: object) -> BacktestConfig:
    fields: dict[str, object] = {
        "strategy_id": "strat-1",
        "strategy_version": "v1",
        "initial_equity": Decimal("100"),
        "cost_model": CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
        "warmup_bars": 0,
        "periods_per_year": 252,
    }
    fields.update(overrides)
    return BacktestConfig.model_validate(fields)


def _result(
    curve: list[EquityPoint], *, config: BacktestConfig, warnings: list[str] | None = None
) -> BacktestResult:
    metrics = compute_metrics(
        equity_curve=curve,
        fills=[],
        initial_equity=config.initial_equity,
        periods_per_year=config.periods_per_year,
    )
    return BacktestResult(
        config=config,
        fills=[],
        equity_curve=curve,
        metrics=metrics,
        warnings=warnings or [],
    )


def test_empty_equity_curve_raises() -> None:
    config = _config()
    metrics = compute_metrics(
        equity_curve=[_point(0, "100")],
        fills=[],
        initial_equity=config.initial_equity,
        periods_per_year=config.periods_per_year,
    )
    result = BacktestResult(config=config, fills=[], equity_curve=[], metrics=metrics)
    with pytest.raises(ValueError):
        build_tearsheet(result)


def test_report_reuses_metrics_without_recomputing() -> None:
    curve = [_point(0, "100"), _point(1, "110"), _point(2, "120")]
    config = _config()
    result = _result(curve, config=config)

    view = build_tearsheet(result)

    assert view.strategy_id == "strat-1"
    assert view.strategy_version == "v1"
    assert view.initial_equity == Decimal("100")
    assert view.final_equity == Decimal("120")
    assert view.total_return.value_pct == result.metrics.total_return_pct
    assert view.total_return.basis == "NET"
    assert view.total_return.method == "TWR"
    assert view.total_return.periods_per_year == 252
    assert view.max_drawdown_pct == result.metrics.max_drawdown_pct
    assert view.sharpe_ratio == result.metrics.sharpe_ratio
    assert view.sortino_ratio == result.metrics.sortino_ratio
    assert view.win_rate_pct == result.metrics.win_rate_pct
    assert view.total_trades == result.metrics.total_trades
    assert view.turnover == result.metrics.turnover
    assert view.basis == "PAPER_SIM"
    assert view.config_hash == config.config_hash()
    assert view.schema_version == "v1"


def test_sharpe_none_passes_through_unfilled() -> None:
    curve = [_point(0, "100"), _point(1, "101")]  # 표본 2개 미만 → compute_metrics가 None
    result = _result(curve, config=_config())

    view = build_tearsheet(result)

    assert view.sharpe_ratio is None
    assert view.sortino_ratio is None
    assert view.win_rate_pct is None


def test_limitations_pass_through_warnings() -> None:
    curve = [_point(0, "100"), _point(1, "100")]
    result = _result(curve, config=_config(), warnings=["zero-cost model"])

    view = build_tearsheet(result)

    assert view.limitations == ["zero-cost model"]


def test_same_input_yields_identical_snapshot() -> None:
    curve = [_point(0, "100"), _point(1, "105"), _point(2, "95")]
    config = _config()
    result_a = _result(curve, config=config)
    result_b = _result(list(curve), config=config)

    view_a = build_tearsheet(result_a)
    view_b = build_tearsheet(result_b)

    assert view_a.model_dump_json() == view_b.model_dump_json()


def test_decimal_fields_serialize_as_strings() -> None:
    curve = [_point(0, "100"), _point(1, "110")]
    result = _result(curve, config=_config())

    payload = build_tearsheet(result).model_dump(mode="json")

    assert isinstance(payload["initial_equity"], str)
    assert isinstance(payload["final_equity"], str)
    assert isinstance(payload["turnover"], str)
    assert isinstance(payload["total_return"]["value_pct"], str)


# ---- DEEPEN(task-3054): negative ----


def test_none_equity_curve_raises() -> None:
    """호출자가 equity_curve에 빈 리스트가 아니라 아예 None을 넘기는
    버그(예: 상위 계층에서 필드를 누락)도 같은 가드가 잡는지 확인한다 —
    `if not result.equity_curve`는 빈 리스트뿐 아니라 None도 거부한다.
    `model_construct`로 pydantic 검증을 우회해 이 계약 위반 상태를 직접
    주입한다(정상 생성자로는 애초에 None이 통과하지 못한다)."""
    config = _config()
    metrics = compute_metrics(
        equity_curve=[_point(0, "100")],
        fills=[],
        initial_equity=config.initial_equity,
        periods_per_year=config.periods_per_year,
    )
    result = BacktestResult.model_construct(
        config=config, fills=[], equity_curve=None, metrics=metrics, warnings=[]
    )
    with pytest.raises(ValueError):
        build_tearsheet(result)


def test_non_paper_sim_metrics_basis_rejected() -> None:
    """metrics.basis가 손상돼(예: 회귀로 LIVE) PAPER_SIM이 아닌 값을 담고
    있어도 `TearsheetView`의 `Literal["PAPER_SIM"]` 제약이 실제로 거부하는지
    확인한다 — 페이퍼 결과를 라이브로 오표기하는 사고를 리포트 DTO 계약이
    막아주는 방어선. `model_copy(update=...)`는 검증을 우회하므로 이미
    계산된 metrics 객체가 이 상태로 오염된 상황을 재현할 수 있다."""
    curve = [_point(0, "100"), _point(1, "110")]
    config = _config()
    metrics = compute_metrics(
        equity_curve=curve,
        fills=[],
        initial_equity=config.initial_equity,
        periods_per_year=config.periods_per_year,
    )
    corrupted_metrics = metrics.model_copy(update={"basis": "LIVE"})
    result = BacktestResult(config=config, fills=[], equity_curve=curve, metrics=corrupted_metrics)

    with pytest.raises(ValueError):  # pydantic.ValidationError는 ValueError의 서브클래스
        build_tearsheet(result)


# ---- DEEPEN(task-3054): 실패 주입 ----


def test_report_config_hash_still_correct_when_config_hash_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: `config.config_hash()` 계산이 실제로 느려지면(예: 회귀로
    canonical_json 정규화 단계에 무거운 검증이 끼어드는 상황)
    `build_tearsheet`이 그 지연을 그대로 감내하면서도 지연 없는 호출과
    동일한 config_hash를 report에 담는지 확인한다 — 아래 p95 단언이
    캐시나 지름길이 아니라 실제 config_hash 호출을 포함한 전체 조립을
    재고 있음을 보장한다.

    호출 여부는 벽시계 경과시간이 아니라 호출 횟수로 증명한다(task-6395)
    — `-n 8` 등 병렬 워커가 코어를 다투는 CI 호스트에서는 10ms 지연이
    타이머/스케줄러 잡음(수 ms)에 묻혀 `elapsed_s < delay_s`로 flake했다.
    """
    curve = [_point(0, "100"), _point(1, "110"), _point(2, "120")]
    config = _config()
    result = _result(curve, config=config)

    original_config_hash = BacktestConfig.config_hash
    call_count = 0

    def _stalled_config_hash(self: BacktestConfig) -> str:
        nonlocal call_count
        call_count += 1
        time.sleep(0.01)
        return original_config_hash(self)

    monkeypatch.setattr(BacktestConfig, "config_hash", _stalled_config_hash)

    stalled_view = build_tearsheet(result)

    monkeypatch.undo()
    baseline_view = build_tearsheet(result)

    assert call_count == 1
    assert stalled_view.config_hash == baseline_view.config_hash


# ---- DEEPEN(task-3054): 수치 성능 단언 ----

# `build_tearsheet`는 이미 계산된 BacktestMetrics를 sha256 config_hash 1회
# 호출과 두 pydantic DTO 생성으로 재포장하는 순수 조립 함수다.
# ADR-2026-09-09-C Decision 1의 "백테스트 1개월 M1 1심볼 3초" 예산 중 리포트
# 뷰 조립이 차지할 몫을 2ms로 상한한다 — 실측 p95(~0.02ms, 벤치마크 기준)
# 대비 충분한 여유를 두면서도, 체결 시뮬레이션 자체(밀리초~초 단위)에 비해
# 무시할 수준이어야 이 리프가 전체 예산을 갉아먹지 않는다는 뜻이다.
_BUDGET_MS = 2.0
_ITERATIONS = 200


def _p95_ms(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)] * 1000


def test_build_tearsheet_p95_latency_within_backtest_budget_slice() -> None:
    """DEEPEN(task-3054): ADR-2026-09-09-C Decision 1 예산 중 리포트 뷰
    조립 몫(2ms)을 실제로 단언한다."""
    curve = [_point(0, "100"), _point(1, "110"), _point(2, "120")]
    config = _config()
    result = _result(curve, config=config)

    samples: list[float] = []
    for _ in range(_ITERATIONS):
        started = time.perf_counter()
        build_tearsheet(result)
        samples.append(time.perf_counter() - started)
    p95_ms = _p95_ms(samples)
    print(f"[BT-12] build_tearsheet p95={p95_ms:.4f}ms budget<{_BUDGET_MS:.0f}ms")
    assert p95_ms < _BUDGET_MS


# ---- DEEPEN(task-3054): 게이트 적색 재현 ----


def test_budget_gate_actually_fails_when_config_hash_stalls_past_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: `config_hash`가 2ms 예산을 실제로 넘기도록 지연을
    주입하면, `test_build_tearsheet_p95_latency_within_backtest_budget_slice`
    와 동일한 단언식이 실제로 `AssertionError`를 내는지(= CI가 실제로
    빨간불이 되는지) 확인한다 — 이 테스트가 없으면 위 단언이 항상 통과하는
    tautology인지 아무도 검증하지 못한다."""
    curve = [_point(0, "100"), _point(1, "110"), _point(2, "120")]
    config = _config()
    result = _result(curve, config=config)

    original_config_hash = BacktestConfig.config_hash

    def _stalled_config_hash(self: BacktestConfig) -> str:
        time.sleep(0.01)  # > 2ms 예산
        return original_config_hash(self)

    monkeypatch.setattr(BacktestConfig, "config_hash", _stalled_config_hash)

    samples: list[float] = []
    for _ in range(5):
        started = time.perf_counter()
        build_tearsheet(result)
        samples.append(time.perf_counter() - started)
    p95_ms = _p95_ms(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _BUDGET_MS
