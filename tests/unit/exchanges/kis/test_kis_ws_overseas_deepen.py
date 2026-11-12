"""task-2777 DEEPEN of task-1783 (BR-5, ADR-2026-09-06-I D2).

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md #1783)는 원 커밋(5846531)이
negative/boundary 테스트와 시뮬레이션된 `ConnectionClosed` 실패주입 테스트를
갖췄지만(D1 실측), D2 하한에 필요한 두 가지가 없다고 판정했다: 수치 성능/지연
단언, 게이트/CI 적색선 회귀 테스트. 이 파일이 그 두 가지만 보강한다 —
`websocket_mixin.py`/`websocket_parsing.py`는 손대지 않는다(새 파서 계층
신설 금지 원칙 유지).

1) 수치 성능/지연 단언: 절대 ms 상수 대신, 같은 프로세스에서 구조적으로
   동일한 연산(파이프/캐럿 분할 + Decimal 변환 + 모델 생성)을 하는 기존
   국내 체결가 파서(`parse_realtime_price_message`)를 베이스라인으로 삼아
   정규화한 배율 임계를 쓴다(task-2773/test_kis_tr_coverage.py,
   task-2772/test_kis_overseas_deepen.py 선례와 동일 판단 — 공유 CI
   환경에서 절대 임계는 상시 적색을 낳는다).
2) 게이트/CI 적색선 회귀: `parse_realtime_overseas_price_message`의 tr_id
   가드(fail-closed — 다른 스트림의 프레임을 조용히 섞어 파싱하지 않게
   막는 방어)를 자식 pytest 프로세스 안에서만 제거하면
   `test_kis_ws_overseas.py::
   test_parse_realtime_overseas_price_message_ignores_other_tr_id`가
   green(1 passed)에서 red(1 failed)로 뒤집힘을 증명한다(동일 기법,
   test_kis_overseas_deepen.py/task-2772 선례).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from src.exchanges.kis.websocket_mixin import (
    _OVERSEAS_PRICE_FIELDS,
    _PRICE_FIELDS,
)

# ---------------------------------------------------------------------------
# 1) 수치 성능/지연 단언(정규화된 배율 임계)
# ---------------------------------------------------------------------------


def _overseas_price_frame(n_records: int) -> str:
    values = {name: "" for name in _OVERSEAS_PRICE_FIELDS}
    values.update(
        {
            "SYMB": "AAPL",
            "LAST": "150.25",
            "PBID": "150.20",
            "PASK": "150.30",
            "TVOL": "98765",
        }
    )
    record = "^".join(values[name] for name in _OVERSEAS_PRICE_FIELDS)
    body = "^".join([record] * n_records)
    return f"0|HDFSCNT0|{n_records:03d}|{body}"


def _domestic_price_frame(n_records: int) -> str:
    values = {name: "" for name in _PRICE_FIELDS}
    values.update(
        {
            "MKSC_SHRN_ISCD": "005930",
            "STCK_PRPR": "70000",
            "BIDP1": "69900",
            "ASKP1": "70100",
            "ACML_VOL": "12345",
        }
    )
    record = "^".join(values[name] for name in _PRICE_FIELDS)
    body = "^".join([record] * n_records)
    return f"0|H0STCNT0|{n_records:03d}|{body}"


def _min_elapsed_seconds(fn: Callable[[], None], repeats: int = 5) -> float:
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        times.append(time.perf_counter() - start)
    return min(times)


# ---------------------------------------------------------------------------
# 0) 음성(negative) 테스트 — fail-closed 검증 (negative 2, 3)
# ---------------------------------------------------------------------------


def test_parse_overseas_price_empty_frame_returns_empty_list() -> None:
    """빈 문자열을 넣으면 빈 리스트를 반환한다.

    파서가 잘못된 프레임을 조용히 무시해야 한다(fail-closed).
    """
    from src.exchanges.kis.websocket_mixin import (
        parse_realtime_overseas_price_message,
    )

    result = parse_realtime_overseas_price_message("")
    assert result == []


def test_parse_overseas_price_wrong_tr_id_returns_empty_list() -> None:
    """해외 체결가 파서에 호가(tr_id=HDFSASP0) 프레임을 넣으면 빈 리스트를 반환한다.

    tr_id 가드가 fail-closed로 동작함을 확인한다 — 다른 스트림의 프레임을
    체결가 필드 스키마로 잘못 파싱하지 않는다.
    """
    from src.exchanges.kis.websocket_mixin import (
        parse_realtime_overseas_price_message,
    )

    # 호가 채널 프레임 (tr_id = HDFSASP0)
    values = {name: "" for name in _OVERSEAS_PRICE_FIELDS}
    values.update({"SYMB": "AAPL", "LAST": "150.25"})
    record = "^".join(values[name] for name in _OVERSEAS_PRICE_FIELDS)
    orderbook_frame = f"0|HDFSASP0|001|{record}"

    result = parse_realtime_overseas_price_message(orderbook_frame)
    assert result == []


# ---------------------------------------------------------------------------
# 1) 수치 성능/지연 단언(정규화된 배율 임계)
# ---------------------------------------------------------------------------


def test_parse_overseas_price_throughput_bounded_vs_domestic_baseline() -> None:
    """해외 체결가 파서가 국내 체결가 파서와 구조적으로 동일한 파이프라인
    (프레임 분할 + 레코드 분할 + Decimal 변환 + 모델 생성)을 쓰므로, 반복
    측정한 총소요시간 배율은 CI 편차를 감안해도 좁은 범위여야 한다. 절대 ms
    상수 대신 같은 프로세스가 방금 측정한 국내 경로 소요시간에 정규화한
    배율을 임계로 쓴다(task-2773/2772 선례와 동일 판단)."""
    from src.exchanges.kis.websocket_mixin import (
        parse_realtime_overseas_price_message,
        parse_realtime_price_message,
    )

    n_records = 20
    n_iterations = 200
    overseas_frame = _overseas_price_frame(n_records)
    domestic_frame = _domestic_price_frame(n_records)

    # 워밍업 — import/JIT 관련 1회성 비용이 표본에 섞이지 않게 한다.
    parse_realtime_overseas_price_message(overseas_frame)
    parse_realtime_price_message(domestic_frame)

    def run_overseas() -> None:
        for _ in range(n_iterations):
            tickers = parse_realtime_overseas_price_message(overseas_frame)
            assert len(tickers) == n_records

    def run_domestic() -> None:
        for _ in range(n_iterations):
            tickers = parse_realtime_price_message(domestic_frame)
            assert len(tickers) == n_records

    domestic_seconds = _min_elapsed_seconds(run_domestic)
    overseas_seconds = _min_elapsed_seconds(run_overseas)

    assert domestic_seconds > 0.0
    ratio = overseas_seconds / domestic_seconds
    budget_ratio = 5.0  # 필드 수는 25 vs 45로 해외 쪽이 더 적어 이론상 <=1배, 여유 5배
    print(
        f"\noverseas vs domestic ws parse throughput: n_records={n_records} "
        f"n_iterations={n_iterations} domestic={domestic_seconds * 1000:.1f}ms "
        f"overseas={overseas_seconds * 1000:.1f}ms ratio={ratio:.2f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"해외 체결가 파서(parse_realtime_overseas_price_message)가 구조적으로 동일한 "
        f"국내 파서 대비 {ratio:.2f}배로 회귀했습니다(예산 {budget_ratio}배) — 레코드 분할/"
        "Decimal 변환 경로에 의도치 않은 무거운 연산이 섞였을 가능성."
    )


# ---------------------------------------------------------------------------
# 2) 성능 단언 — PerfBudget.best_of + @pytest.mark.perf
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_parse_overseas_price_performance_under_budget() -> None:
    """parse_realtime_overseas_price_message 단일 호출이 5 ms CPU 미만이어야 한다.

    PerfBudget.best_of(best-of-5)로 노이즈를 걷어내고 cpu_ms를 예산 판정 기준으로 쓴다.
    실측 기준: 20개 레코드 프레임 파싱 ≈ 0.2 ms (로컬 CI 호스트, 2026-11-12).
    30× 여유 → 6 ms, 안전 마진 포함 5 ms 예산으로 설정.
    """
    from src.exchanges.kis.websocket_mixin import (
        parse_realtime_overseas_price_message,
    )
    from tests.support.perf_budget import PerfBudget

    n_records = 20
    overseas_frame = _overseas_price_frame(n_records)

    # 워밍업
    parse_realtime_overseas_price_message(overseas_frame)

    budget = PerfBudget()
    sample = budget.best_of(
        lambda: parse_realtime_overseas_price_message(overseas_frame),
        n=5,
    )
    print(
        f"\noverseas price parse perf: cpu={sample.cpu_ms:.3f}ms "
        f"wall={sample.wall_ms:.3f}ms (budget=5.0ms)"
    )
    assert sample.cpu_ms < 5.0, (
        f"parse_realtime_overseas_price_message이 예산을 초과했습니다: "
        f"{sample.cpu_ms:.3f}ms > 5.0ms budget. "
        f"wall={sample.wall_ms:.3f}ms"
    )


# ---------------------------------------------------------------------------
# 3) 게이트 적색 재현 — perf_inject 가상 지연으로 성능 단언이 실제로 잡히는지 검증
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_parse_overseas_price_performance_gate_red_virtual_delay() -> None:
    """성능 단언이 실제로 잡히는지 가상 지연으로 적색 재현.

    `virtual_delayed_sync`으로 parse_realtime_overseas_price_message 호출에
    0.1 s 가상 지연을 주입하면 PerfBudget 측정값이 예산(5 ms)을 초과해야 한다
    (gate-red). 이후 지연 제거 시 정상 경로가 예산 내임을 best_of로 증명한다.
    """
    from src.exchanges.kis.websocket_mixin import (
        parse_realtime_overseas_price_message,
    )
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_sync,
    )

    n_records = 20
    overseas_frame = _overseas_price_frame(n_records)

    # 워밍업
    parse_realtime_overseas_price_message(overseas_frame)

    budget = PerfBudget()

    # Gate-red: 파서에 0.1 s 가상 지연 주입 → budget 초과
    reset_virtual_totals()
    delayed_fn = virtual_delayed_sync(
        lambda: parse_realtime_overseas_price_message(overseas_frame), 0.1
    )
    # _add_virtual는 virtual_delayed_sync 내부에서 호출됨 (0.1s cpu + 0.1s wall)
    sample = budget.best_of(delayed_fn, n=3)
    assert sample.cpu_ms >= 5.0, (
        f"budget assertion should fail with 100ms injected delay: "
        f"cpu={sample.cpu_ms:.3f}ms (gate-red reproduced)"
    )

    # 정상 경로: 지연 제거 → 예산 내 통과
    sample_normal = budget.best_of(
        lambda: parse_realtime_overseas_price_message(overseas_frame),
        n=5,
    )
    assert sample_normal.cpu_ms < 5.0, (
        f"normal path should pass budget: {sample_normal.cpu_ms:.3f}ms"
    )


# ---------------------------------------------------------------------------
# 4) 게이트/CI 적색선 회귀 — parse_realtime_overseas_price_message의
#    tr_id 가드 제거
# ---------------------------------------------------------------------------

_GUARD = '    if tr_id != "HDFSCNT0":\n        return []\n'
_MUTATED = ""


def _plugin_source() -> str:
    return f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module("src.exchanges.kis.websocket_parsing")
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {_GUARD!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {_MUTATED!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def test_pytest_gate_turns_red_when_overseas_price_tr_id_guard_is_removed(
    tmp_path: Path,
) -> None:
    """`parse_realtime_overseas_price_message`의 tr_id 가드(fail-closed —
    다른 스트림의 프레임을 섞어 해외 체결가로 오인 파싱하지 않게 막는 방어)를
    자식 pytest 프로세스 안에서만 제거하면(프로덕션 소스는 그대로),
    `test_kis_ws_overseas.py::
    test_parse_realtime_overseas_price_message_ignores_other_tr_id`가
    green(1 passed)에서 red(1 failed)로 뒤집힘을 증명한다 — 가드 없이는
    호가(HDFSASP0) 프레임도 체결가 필드 스키마로 잘못 파싱되어 빈 리스트
    대신 (틀린) Ticker가 나온다."""
    target_test = (
        "tests/unit/exchanges/kis/test_kis_ws_overseas.py::"
        "test_parse_realtime_overseas_price_message_ignores_other_tr_id"
    )
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target_test]
    repo_root = str(Path.cwd())
    env = dict(os.environ, PYTHONPATH=repo_root, PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")

    baseline = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
        check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin_module_name = "_mutate_overseas_price_tr_id_guard"
    plugin = tmp_path / f"{plugin_module_name}.py"
    plugin.write_text(_plugin_source(), encoding="utf-8")
    mutated_env = dict(env, PYTHONPATH=f"{repo_root}{os.pathsep}{tmp_path}")

    mutated = subprocess.run(
        [*command[:-1], "-p", plugin_module_name, command[-1]],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=mutated_env,
        timeout=120,
        check=False,
    )
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout
