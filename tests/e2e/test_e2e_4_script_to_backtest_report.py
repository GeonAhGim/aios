"""E2E-4 -- 전략 스크립트 -> 즉시 백테스트 -> 리포트: DSL 컴파일 -> 인터프리터
실행 -> 벡터 신호 -> 체결 엔진 -> 성과/오버피팅 지표 -> AI-10 실험 원장 저장.

Spec: docs/design/ADR-2026-09-24-A-mvp1-exit-order-and-fleet-kit.md Decision 3
("깊이 미판정 리프를 개별 재QA하는 대신 E2E 시나리오로 대체 검증").

이 시나리오가 커버하는 명세 리프:
- DSL-2~7 (`src/core/script/{grammar,typing,analysis,ir}`) -- 렉서->파서->
  타입체커->lookahead->자원체커->IR lowering 파이프라인 (DSL-12
  `artifact/compile.py::compile_source`가 이 순서로 조립).
- DSL-8 (`src/core/script/runtime/interpreter.py::execute`) -- 컴파일된 IR을
  실제로 실행해 `signal` 바인딩(bar별 bool 시리즈)을 산출.
- DSL-12 (`artifact/compile.py`) -- `ScriptCompileError` 4종 taxonomy 중
  `SCRIPT_SYNTAX`/`SCRIPT_RESOURCE_LIMIT` 실패 주입.
- BT-1 (`domain/models_v2.py::BacktestConfigV2`) -- 체결 리얼리즘 계약(0비용
  구성으로 다른 리프의 산술이 섞이지 않게 고정).
- BT-2/BT-3/BT-4/BT-5 (`domain/fill/{slippage,commission,latency,
  partial_fill}.py`) -- 실제 체결가/수수료/체결봉/부분체결 계산 경로.
- BT-7 (`domain/magnifier.py`) -- `magnifier_tf=None` 폴백 가격 경로(항상
  시가로 시작).
- BT-9 (`domain/reproducibility.py::reproducibility_key`/`config_hash`) --
  재현성 키 조립.
- BT-10 (`application/quick_backtest.py::run_quick_backtest`) -- 봉 순회/
  포지션·현금 갱신/결과 조립(이벤트 엔진 본체, 벡터 경로가 그대로 위임).
- BT-15a/BT-15b (`vector/{signals,fills}.py`) -- DSL `execute()`가 만든
  `Series`를 `BoolSignal`로 벡터화하고, `VectorSignal`을 통해 BT-2~6 이벤트
  엔진에 그대로 넘기는 다리(I-05: 벡터 경로와 이벤트 경로가 같은 체결 로직을
  공유 -- 재구현하지 않음).
- L34 (`domain/overfitting.py::deflated_sharpe`/`pbo_cscv`) -- Deflated
  Sharpe Ratio, PBO(CSCV) 오버피팅 지표.
- AI-10 (`src/foundation/experiments/{contracts/v1.py,
  adapters/postgres_repository.py}`, migration `c3f8a1d29b6e`) -- 실험 원장
  append-only(WORM) 저장/조회, 위조 거부.
- I-04 (재현성: `reproducibility_key`가 4개 입력을 있는 그대로 조립) / I-05
  (백테스트-실거래 체결 로직 공유: 벡터 엔진이 이벤트 엔진을 재구현하지 않음).

기존 `tests/e2e/(test_e2e_2_watchdog_liquidate, test_order_execution_settlement,
test_restart_recovery)` 어느 것도 스크립트 DSL/백테스트/오버피팅 축을 다루지
않는다 -- 이 파일은 그 축만 새로 잇는다. `tests/e2e/conftest.py`의 `pool`
픽스처를 그대로 재사용한다.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.script.analysis.resources import ResourceLimits
from src.core.script.artifact.compile import ScriptCompileError, compile_source
from src.core.script.runtime.interpreter import execute
from src.core.script.runtime.series import Series
from src.foundation.backtest.domain.models_v2 import (
    AdjustmentsConfig,
    BacktestConfigV2,
    CostsConfig,
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    VenueTierCommission,
)
from src.foundation.backtest.domain.overfitting import deflated_sharpe, pbo_cscv
from src.foundation.backtest.domain.reproducibility import config_hash, reproducibility_key
from src.foundation.backtest.vector.fills import VectorSignal, run_vector_backtest
from src.foundation.backtest.vector.signals import bool_from_series
from src.foundation.experiments.adapters.postgres_repository import PostgresExperimentRepository
from src.foundation.experiments.contracts.v1 import Experiment, ExperimentKind
from src.foundation.market_data.api import CandleColumns
from src.foundation.market_data.contracts.v1 import Timeframe
from tests.integration.conftest import create_test_tenant

REGISTRY_VERSION = "e" * 64

# 6봉 결정론적 시나리오: close가 상승->상승->하락->상승->상승으로 움직여
# go_long/go_flat 신호가 각각 정확히 두 번씩 정의된 값으로 발화한다(직전 대비
# 비교라 0번째 봉은 na).
_BASE_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
_OPENS = [Decimal(v) for v in (100, 104, 109, 107, 111, 114)]
_CLOSES = [Decimal(v) for v in (100, 105, 110, 108, 112, 115)]

SCRIPT_SOURCE = (
    "input close: series<float> = 0\n"
    "signal go_long = close > close[1]\n"
    "signal go_flat = close < close[1]\n"
)


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def _candle_columns() -> CandleColumns:
    n = len(_CLOSES)
    ts = [_BASE_TS.replace(minute=i) for i in range(n)]
    highs = [max(o, c) for o, c in zip(_OPENS, _CLOSES, strict=True)]
    lows = [min(o, c) for o, c in zip(_OPENS, _CLOSES, strict=True)]
    return CandleColumns(
        ts=ts,
        open=list(_OPENS),
        high=highs,
        low=lows,
        close=list(_CLOSES),
        volume=[Decimal(1000)] * n,
        quote_volume=[None] * n,
    )


def _zero_cost_config() -> BacktestConfigV2:
    return BacktestConfigV2(
        slippage=FixedSlippage(bps=Decimal("0")),
        commission=VenueTierCommission(
            venue="paper", maker_bps=Decimal("0"), taker_bps=Decimal("0"), min_fee=Decimal("0")
        ),
        latency_ms=0,
        partial_fill=PartialFillConfig(max_participation_pct=Decimal("1")),
        order_types=OrderTypesConfig(limit=False, stop=False, oco=False, trailing=False),
        magnifier_tf=None,
        costs=CostsConfig(funding=False, borrow_apr=None),
        adjustments=AdjustmentsConfig(splits=False, dividends=False),
        calendar="24x7",
    )


# ---- 본선 경로 -- DSL 컴파일 -> 실행 -> 벡터 백테스트 -> 지표 -> 실험 원장 ----


async def test_script_compile_to_backtest_report_persists_reproducible_experiment(
    pool: asyncpg.Pool,
) -> None:
    """전체 경로 실증: 실제 `compile_source`(DSL-2~7)로 스크립트를 컴파일하고,
    실제 `execute`(DSL-8)로 인터프리터를 돌려 얻은 `signal` 바인딩을
    `bool_from_series`(BT-15a)로 벡터화한 뒤, `run_vector_backtest`(BT-15b)가
    BT-2~6 이벤트 엔진(`run_quick_backtest`)에 그대로 위임해 체결/현금/포지션을
    갱신한다(I-05: 재구현 없음). 결과의 정확한 Decimal 값을 손으로 미리 유도한
    값과 대조하고, 성과/오버피팅 지표(L34)를 계산한 뒤 재현성 키(BT-9)와 함께
    AI-10 실험 원장에 append하고, WORM 위조 거부까지 실증한다."""
    compiled = compile_source(SCRIPT_SOURCE, registry_version=REGISTRY_VERSION)

    result = execute(
        compiled.ir,
        bar_count=len(_CLOSES),
        inputs={"close": Series.of_floats(float(c) for c in _CLOSES)},
    )

    go_long_series = result.signals["go_long"]
    go_flat_series = result.signals["go_flat"]
    assert isinstance(go_long_series, Series)
    assert isinstance(go_flat_series, Series)
    go_long = bool_from_series(go_long_series)
    go_flat = bool_from_series(go_flat_series)
    # bar 0은 close[1]이 na라 두 신호 모두 na -- na는 "발화 아님"으로 처리되므로
    # (BoolSignal 규약) 진입/청산 어느 쪽도 트리거하지 않는다.
    assert go_long.na[0] and go_flat.na[0]
    assert (tuple(go_long.na.tolist()), tuple(go_long.values.tolist())) == (
        (True, False, False, False, False, False),
        (False, True, True, False, True, True),
    )
    assert (tuple(go_flat.na.tolist()), tuple(go_flat.values.tolist())) == (
        (True, False, False, False, False, False),
        (False, False, False, True, False, False),
    )

    vector_signal = VectorSignal(entries=go_long, exits=go_flat, quantity=Decimal("1"))
    config = _zero_cost_config()
    columns = _candle_columns()

    bt = run_vector_backtest(
        config,
        columns,
        vector_signal,
        timeframe=Timeframe.M1,
        initial_cash=Decimal("100000"),
    )

    # 손으로 유도한 정확값(Decimal) -- bar1 진입신호 -> bar2 시가(109) 매수 체결,
    # bar3 청산신호 -> bar4 시가(111) 매도 체결, bar4 재진입신호 -> bar5 시가(114)
    # 매수 체결(마지막 봉이라 포지션 보유한 채 종료).
    assert len(bt.fills) == 3
    buy1, sell1, buy2 = bt.fills
    assert buy1.price == Decimal("109") and buy1.quantity == Decimal("1")
    assert buy1.commission == Decimal("0") and buy1.bar_index == 2
    assert sell1.price == Decimal("111") and sell1.quantity == Decimal("1")
    assert sell1.commission == Decimal("0") and sell1.bar_index == 4
    assert buy2.price == Decimal("114") and buy2.quantity == Decimal("1")
    assert buy2.commission == Decimal("0") and buy2.bar_index == 5

    assert bt.cash == Decimal("99888")
    assert bt.position_quantity == Decimal("1")
    assert bt.final_equity == Decimal("100003")
    assert bt.equity_curve == (
        Decimal("100000"),
        Decimal("100000"),
        Decimal("100001"),
        Decimal("99999"),
        Decimal("100002"),
        Decimal("100003"),
    )
    assert bt.funding_cost == Decimal("0")
    assert bt.borrow_cost == Decimal("0")
    assert bt.expired_orders == 0

    total_return = (bt.final_equity - Decimal("100000")) / Decimal("100000")
    assert total_return == Decimal("0.00003")

    # ---- L34 오버피팅 지표(실제 함수 호출, 논문 수치 예제와 대조) -----------
    sr_hat = Decimal("2.5") / Decimal(250).sqrt()
    sr_var = Decimal("0.5") / Decimal(250)
    dsr = deflated_sharpe(sr_hat, 100, 1250, Decimal(-3), Decimal(10), sr_var)
    assert float(dsr) == pytest.approx(0.9004, abs=1e-4)

    # 한 열이 모든 행에서 나머지를 완전히 지배 -- 모든 CSCV 분할에서 그 열이
    # IS/OOS 양쪽 모두 최고이므로 lambda_c > 0 항상 성립 -> PBO 정확히 0.
    dominant_matrix = [[Decimal(10), Decimal(1), Decimal(0)] for _ in range(8)]
    pbo = pbo_cscv(dominant_matrix, 4)
    assert pbo == Decimal(0)

    # ---- BT-9 재현성 키 + AI-10 실험 원장 append -----------------------------
    data_lineage_hash = hashlib.sha256(
        repr(tuple(columns.close)).encode("utf-8")
    ).hexdigest()
    rollup_version = "rollup-v1"
    repro_key = reproducibility_key(
        script_hash=compiled.script_hash,
        data_lineage_hash=data_lineage_hash,
        rollup_version=rollup_version,
        config=config,
    )
    assert repro_key == reproducibility_key(
        script_hash=compiled.script_hash,
        data_lineage_hash=data_lineage_hash,
        rollup_version=rollup_version,
        config=config,
    )
    assert config_hash(config) == config_hash(config)  # 결정론(BT-9 canonical_json)

    inputs_hash = hashlib.sha256(
        (compiled.script_hash + data_lineage_hash).encode("utf-8")
    ).hexdigest()

    tenant_id: UUID = await create_test_tenant(pool)
    experiment_id = uuid4()
    experiment = Experiment(
        experiment_id=experiment_id,
        tenant_id=tenant_id,
        reproducibility_key=repro_key,
        kind=ExperimentKind.BACKTEST,
        inputs_hash=inputs_hash,
        metrics={
            "final_equity": str(bt.final_equity),
            "total_return": str(total_return),
            "deflated_sharpe": str(dsr),
            "pbo_cscv": str(pbo),
        },
        artifacts=(),
        parent_id=None,
        created_by=tenant_id,
        created_at=datetime.now(timezone.utc),
    )
    repo = PostgresExperimentRepository(pool)
    await repo.append(experiment)

    fetched = await repo.get(tenant_id, experiment_id)
    assert fetched is not None
    assert fetched.reproducibility_key == repro_key
    assert fetched.inputs_hash == inputs_hash
    assert fetched.metrics["final_equity"] == str(bt.final_equity)

    by_key = await repo.find_by_reproducibility_key(tenant_id, repro_key)
    assert len(by_key) == 1
    assert by_key[0].experiment_id == experiment_id

    # ---- 실패 주입 #3 -- 불일치(mismatch): WORM 위조 시도가 실제로 거부됨 ----
    raw_conn = await asyncpg.connect(_asyncpg_dsn())
    try:
        with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)) as exc_info:
            async with raw_conn.transaction():
                await raw_conn.execute("SET ROLE aios_app")
                await raw_conn.execute(
                    "UPDATE experiments SET inputs_hash = $1 WHERE experiment_id = $2",
                    "f" * 64,
                    experiment_id,
                )
        if isinstance(exc_info.value, asyncpg.RaiseError):
            assert "append-only" in str(exc_info.value).lower()
    finally:
        await raw_conn.close()

    unchanged = await repo.get(tenant_id, experiment_id)
    assert unchanged is not None
    assert unchanged.inputs_hash == inputs_hash, "WORM 위조가 조용히 반영됨 -- append-only 위반"


# ---- 실패 주입 #1 -- 거부(rejection): 문법 오류 스크립트는 컴파일이 거부된다 --


def test_syntax_error_script_is_rejected_with_script_syntax_code() -> None:
    """실패 주입(거부) -- 파서(DSL-3)가 실패하는 스크립트는 `compile_source`가
    4종 taxonomy 중 `SCRIPT_SYNTAX`로 감싸 (line, col)과 함께 거부해야 한다.
    IR lowering까지 도달해 다른 경로로 죽거나 조용히 통과하면 안 된다."""
    broken_source = "input close: series<float> = 0\nsignal go_long = \n"

    with pytest.raises(ScriptCompileError) as exc_info:
        compile_source(broken_source, registry_version=REGISTRY_VERSION)

    assert exc_info.value.code == "SCRIPT_SYNTAX"
    assert exc_info.value.line >= 1
    assert exc_info.value.col >= 1


# ---- 실패 주입 #2 -- 거부(rejection): 자원 한도를 넘는 스크립트는 컴파일이 거부된다 --


def test_resource_limit_exceeded_script_is_rejected_with_script_resource_limit_code() -> None:
    """실패 주입(거부) -- 정상적으로 파싱/타입체크/lookahead를 통과하는 스크립트도
    DSL-6 자원 한도(`max_series`)를 넘으면 `SCRIPT_RESOURCE_LIMIT`로 거부돼야
    한다. `SCRIPT_SOURCE`는 series 선언이 3개(input 1 + signal 2)라
    `max_series=1`이면 반드시 넘친다."""
    tight_limits = ResourceLimits(max_series=1)

    with pytest.raises(ScriptCompileError) as exc_info:
        compile_source(SCRIPT_SOURCE, registry_version=REGISTRY_VERSION, limits=tight_limits)

    assert exc_info.value.code == "SCRIPT_RESOURCE_LIMIT"
    assert exc_info.value.line >= 1
    assert exc_info.value.col >= 1
