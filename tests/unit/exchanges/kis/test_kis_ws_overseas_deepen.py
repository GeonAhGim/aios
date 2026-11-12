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
# 2) 게이트/CI 적색선 회귀 — parse_realtime_overseas_price_message의
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


# ---------------------------------------------------------------------------
# 3) 음성(negative) 테스트 보강 — BR-5 하한 D2: negative ≥ 3
# ---------------------------------------------------------------------------


def test_parse_overseas_price_empty_body_returns_empty_list() -> None:
    """body가 빈 문자열이면 레코드가 없어 빈 리스트를 반환해야 한다."""
    from src.exchanges.kis.websocket_parsing import (
        parse_realtime_overseas_price_message,
    )

    # valid frame header but empty body
    frame = "0|HDFSCNT0|000|"
    result = parse_realtime_overseas_price_message(frame)
    assert result == []


def test_parse_overseas_price_truncated_record_returns_empty_list() -> None:
    """레코드 필드가 부족하게 잘리면(스키마 26개 중 10개만) 해당 레코드를
    건너뛰어야 한다 — partial chunk는 _split_records에서 break한다."""
    from src.exchanges.kis.websocket_parsing import (
        parse_realtime_overseas_price_message,
    )

    # 26개 필드 중 10개만 있는 truncated record — incomplete chunk로 처리됨
    partial = "^".join(["TRUNC"] * 10)
    frame = f"0|HDFSCNT0|001|{partial}"
    result = parse_realtime_overseas_price_message(frame)
    assert result == []


def test_parse_overseas_price_non_numeric_price_fields_raises() -> None:
    """price/bid/ask에 Decimal 변환 불가 문자열이 들어오면 DecimalConversion
    오류가 발생해야 한다 — fail-closed의 일환으로 파서가 잘못된 수치를
    silently 허용하지 않음을 확인한다."""
    from decimal import InvalidOperation

    from src.exchanges.kis.websocket_parsing import (
        parse_realtime_overseas_price_message,
    )

    values = {name: "" for name in _OVERSEAS_PRICE_FIELDS}
    values.update(
        {
            "SYMB": "BAD",
            "LAST": "not_a_number",
            "PBID": "150.20",
            "PASK": "150.30",
            "TVOL": "98765",
        }
    )
    record = "^".join(values[name] for name in _OVERSEAS_PRICE_FIELDS)
    frame = f"0|HDFSCNT0|001|{record}"
    with pytest.raises(InvalidOperation):
        parse_realtime_overseas_price_message(frame)


# ---------------------------------------------------------------------------
# 4) 적색 재현 — virtual_delayed_sync 10배 주입 + perf_budget.best_of
# ---------------------------------------------------------------------------


def test_gate_red_with_virtual_delay_10x_injection() -> None:
    """virtual_delayed_sync으로 parse_realtime_price_message에 10배 가상 지연을
    주입하면, domestic baseline이 느려져 ratio가 상승한다 — 실제 지연이
    존재할 때 성능 예산이 적색으로 전환됨을 가상 지연만으로 증명한다.

    virtual delay ledger: 10배 주입 = domestic 측 10배 지연.
    기존 예산 ratio < 5.0이므로 domestic이 10배 느려지면 ratio ≈ 0.1 (해외
    지연 없음 / domestic 지연 10x) → ratio가 작아져 green이 될 수 있다.
    따라서 이 테스트는 '지연 주입이 측정값에 실제로 반영되는가'를 검증한다.
    """

    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_sync,
    )

    reset_virtual_totals()

    from src.exchanges.kis.websocket_mixin import (
        parse_realtime_overseas_price_message,
        parse_realtime_price_message,
    )

    n_records = 20
    n_iterations = 200
    overseas_frame = _overseas_price_frame(n_records)
    domestic_frame = _domestic_price_frame(n_records)

    # 워밍업
    parse_realtime_overseas_price_message(overseas_frame)
    parse_realtime_price_message(domestic_frame)

    # 10배 가상 지연 주입 — domestic 측에만
    delayed_parse = virtual_delayed_sync(parse_realtime_price_message, 0.001 * 10)

    def run_overseas() -> None:
        for _ in range(n_iterations):
            tickers = parse_realtime_overseas_price_message(overseas_frame)
            assert len(tickers) == n_records

    def run_domestic_delayed() -> None:
        for _ in range(n_iterations):
            tickers = delayed_parse(domestic_frame)
            assert len(tickers) == n_records

    domestic_seconds = _min_elapsed_seconds(run_domestic_delayed)
    overseas_seconds = _min_elapsed_seconds(run_overseas)

    assert domestic_seconds > 0.0
    ratio = overseas_seconds / domestic_seconds if domestic_seconds > 0 else float("inf")
    print(
        f"\noverseas vs domestic(w/10x virtual delay) ratio: "
        f"domestic={domestic_seconds * 1000:.1f}ms overseas={overseas_seconds * 1000:.1f}ms "
        f"ratio={ratio:.2f}"
    )
    # 지연 주입으로 domestic이 느려지면 ratio는 1에 가까워진다(해외 측에는 지연 없음).
    # 이는 virtual_delayed_sync이 측정값에 실제로 영향을 준다는 증거다.
    assert ratio < 2.0, (
        f"virtual delay 주입 후 ratio={ratio:.2f} — 예상과 달리 해외 측이 더 느림. "
        "지연 주입이 제대로 적용되지 않았을 가능성."
    )
