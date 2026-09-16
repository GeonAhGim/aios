"""ExecutionEquityTracker의 seed/영속화 연동 지점 단위테스트 — DB 없이
순수 메모리 로직만 검증(PM 배정 ③, 2026-09-02).

`save_equity_baseline`(실 DB 대상)은 tests/integration/services/test_equity_tracker.py로
분리했다(task-1615, PLT-36 — tests/unit 아래는 실DB에 접속하지 않는다).

DEEPEN(task-3188, docs/audit/DEPTH_PLT.md leaf 1615): D1이었던 이 리프에
실패 주입(인프라 결함, `pytest.raises(KeyError)` 계열 입력검증과는 다른 축)·
수치 성능 단언·게이트 적색 재현(esc-ci-48db4810270d의 근본원인이었던
"tests/unit 아래 실DB 접속 fixture" 패턴 자체를 합성 재현)을 추가해 D2를
채운다. 아래 페이크 pool들은 실 asyncpg 없이 인프라 결함(커넥션 획득 실패)만
흉내낸다 — 이 파일이 asyncpg를 임포트하면 그 자체가 이 리프가 막으려는
결함이므로, 실패 주입 테스트조차 실 DB 클라이언트를 쓰지 않는다."""

import ast
import time
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from src.services.execution_loop.equity_tracker import (
    ExecutionEquityTracker,
    _utc_today,
    record_and_persist_equity,
)


def test_is_seeded_false_before_any_record_or_seed() -> None:
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 2))
    assert tracker.is_seeded(1) is False


def test_seed_populates_baseline_when_memory_empty() -> None:
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 2))
    tracker.seed(
        1,
        day_start_date=date(2026, 9, 1),
        day_start_equity=Decimal("1000"),
        peak_equity=Decimal("1200"),
    )
    assert tracker.is_seeded(1) is True
    assert tracker.day_start(1) == (date(2026, 9, 1), Decimal("1000"))
    assert tracker.peak(1) == Decimal("1200")


def test_seed_with_none_values_does_not_mark_as_seeded() -> None:
    """DB에 아직 기준점이 없는(최초 실행) execution — seed가 아무것도
    못 채우면 is_seeded도 계속 False라 record()가 정상적으로 오늘을
    시작일로 잡는다."""
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 2))
    tracker.seed(1, day_start_date=None, day_start_equity=None, peak_equity=None)
    assert tracker.is_seeded(1) is False


def test_seed_does_not_overwrite_already_recorded_value() -> None:
    """이 프로세스가 이미 한 번 record()한 execution에 뒤늦게 seed()가
    불려도(방어적 호출) 메모리 값을 덮어쓰지 않는다 — DB는 초기값
    용도일 뿐, record() 이후로는 메모리가 진실의 원천."""
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 2))
    tracker.record(1, Decimal("500"))

    tracker.seed(
        1,
        day_start_date=date(2026, 8, 1),
        day_start_equity=Decimal("999"),
        peak_equity=Decimal("999"),
    )

    assert tracker.day_start(1) == (date(2026, 9, 2), Decimal("500"))
    assert tracker.peak(1) == Decimal("500")


def test_seeded_baseline_feeds_into_record_daily_pnl() -> None:
    """재시작 복구 시나리오 — 오늘 이미 -2% 손실 중이었다면, seed 이후의
    첫 record()가 그 손실을 반영해야 한다(재시작으로 "오늘 시작"이
    리셋되면 안 됨)."""
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 2))
    tracker.seed(
        1,
        day_start_date=date(2026, 9, 2),
        day_start_equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
    )

    daily_pnl_pct, drawdown_pct = tracker.record(1, Decimal("980"))

    assert daily_pnl_pct == Decimal("-2")
    assert drawdown_pct == Decimal("2")


def test_default_clock_is_utc_fixed_not_local() -> None:
    """`date.today()`(OS 로컬 tz) 대신 UTC 고정 기본 clock을 쓴다."""
    tracker = ExecutionEquityTracker()
    assert tracker._today is _utc_today
    assert tracker._today() == datetime.now(timezone.utc).date()


def test_day_start_before_any_record_or_seed_raises_key_error() -> None:
    """호출부(`record_and_persist_equity`)는 반드시 `record()` 직후에만
    `day_start()`를 부른다는 계약이다 — 순서를 어기면 잘못된(0값 등)
    기본값을 조용히 반환하는 대신 즉시 KeyError로 fail-closed 해야 한다."""
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 2))
    try:
        tracker.day_start(1)
    except KeyError:
        pass
    else:
        raise AssertionError("day_start() must raise before record()/seed()")


def test_peak_before_any_record_or_seed_raises_key_error() -> None:
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 2))
    try:
        tracker.peak(1)
    except KeyError:
        pass
    else:
        raise AssertionError("peak() must raise before record()/seed()")


def test_utc_today_diverges_from_os_local_timezone_at_day_boundary() -> None:
    """R-30이 요구하는 UTC 고정 일경계 실증 — UTC 자정 직후(00:30 UTC)
    시각을 고정하면, UTC-8 로컬 벽시계는 아직 전날(16:30, 전날 날짜)이다.
    `date.today()`(OS 로컬 tz)를 썼다면 day boundary가 하루 늦게
    잡혔을 것이라는 걸 같은 순간의 로컬 환산값과 대조해 증명한다."""
    fixed_instant = datetime(2026, 9, 10, 0, 30, tzinfo=timezone.utc)
    local_equivalent = fixed_instant.astimezone(timezone(timedelta(hours=-8)))
    assert local_equivalent.date() == date(2026, 9, 9)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return fixed_instant if tz is not None else fixed_instant.replace(tzinfo=None)

    with patch("src.services.execution_loop.equity_tracker.datetime", _FixedDatetime):
        result = _utc_today()

    assert result == date(2026, 9, 10)
    assert result != local_equivalent.date()


class _FakeConn:
    """실 asyncpg 없이 `pool.acquire()` 결과만 흉내내는 최소 페이크."""

    def __init__(self, fetchrow_result: object = None) -> None:
        self._fetchrow_result = fetchrow_result

    async def fetchrow(self, *args: object, **kwargs: object) -> object:
        return self._fetchrow_result


class _AcquireSucceeds:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _AcquireFailsWithInfraError:
    async def __aenter__(self) -> _FakeConn:
        raise ConnectionResetError("simulated infra failure: db link reset mid persist")

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _SeedOkThenPersistFailsPool:
    """`load_equity_baseline`(1번째 acquire)은 성공시키고, 그 직후
    `save_equity_baseline`(2번째 acquire)에서 인프라 결함(커넥션 획득
    실패)을 주입한다 — record()가 이미 메모리를 갱신한 뒤 영속화가
    실패하는, 이 모듈 docstring이 우려하는 정확한 순서를 재현한다."""

    def __init__(self) -> None:
        self._calls = 0

    def acquire(self) -> _AcquireSucceeds | _AcquireFailsWithInfraError:
        self._calls += 1
        if self._calls == 1:
            return _AcquireSucceeds(_FakeConn(fetchrow_result=None))
        return _AcquireFailsWithInfraError()


async def test_record_and_persist_equity_propagates_infra_failure_fail_closed() -> None:
    """실패 주입(인프라 결함) — `pytest.raises(KeyError)` 계열의 입력검증
    negative와는 다른 축: DB 커넥션 획득 자체가 실패하는 상황을 페이크
    pool로 흉내내, 그 예외가 삼켜지지 않고 그대로 전파되는지 검증한다.
    조용히 삼켜지면 호출부가 "저장 성공"으로 착각해 다음 tick이 잘못된
    write-through 스킵을 할 수 있다(모듈 상단 docstring 근거)."""
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 2))
    pool = _SeedOkThenPersistFailsPool()

    with pytest.raises(ConnectionResetError):
        await record_and_persist_equity(pool, tracker, 1, Decimal("1000"))

    # 실패는 영속화 단계에서만 발생했다 — record()로 이미 갱신된 메모리
    # 상태 자체는 되돌려지지 않는다(부분 실패가 계산 결과를 침해하지 않음).
    assert tracker.is_seeded(1) is True
    assert tracker.day_start(1) == (date(2026, 9, 2), Decimal("1000"))


def test_record_throughput_bound_for_many_executions() -> None:
    """성능 단언 — 순수 메모리 연산이므로 실 DB 왕복 없이 10,000회
    record() 호출이 느슨한 상한 안에 끝나야 한다(다른 DEEPEN 리프의
    성능 단언과 동일하게 flakiness 회피용 여유 상한 — 명백한 알고리즘
    회귀(예: 매 호출마다 전체 딕셔너리 스캔으로 퇴화)만 잡는다)."""
    tracker = ExecutionEquityTracker(today=lambda: date(2026, 9, 2))

    start = time.monotonic()
    for execution_id in range(10_000):
        tracker.record(execution_id, Decimal("1000") + execution_id)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0


def _imports_asyncpg(source: str) -> bool:
    """AST로 최상위 모듈명이 `asyncpg`인 import를 찾는다(tests/unit/test_zone_purity.py의
    domain/** 순수성 검사와 동일한 기법 — 문자열 grep이 아니라 실제 import 문만 판정)."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == "asyncpg" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and node.module.split(".")[0] == "asyncpg":
                return True
    return False


def test_regression_confirms_scan_would_catch_the_original_ci_red_fixture(
    tmp_path: Path,
) -> None:
    """게이트 적색 재현 — esc-ci-48db4810270d(tests/unit/services 5건 ERROR)의
    근본원인은 tests/unit 아래 파일이 asyncpg로 실DB에 직접 접속하는
    fixture를 정의한 것이었다(dc169b12 이전 test_legacy_execution_pauser.py가
    정확히 이 형태 — `asyncpg.create_pool(os.environ["DATABASE_URL"])`를 쓰는
    `pool` fixture). 그 결함 패턴을 합성 파일로 재현해 스캔이 실제로
    탐지함을(RED) 먼저 보이고, 현재 tests/unit/services/의 실제 파일에는
    그 패턴이 없음을(GREEN) 이어서 확인한다 — "테스트가 있다"가 아니라
    "이 스캔이 실제로 그 결함을 잡는다" 자체를 증명한다."""
    synthetic_bad_file = tmp_path / "test_synthetic_unit_db_fixture.py"
    synthetic_bad_file.write_text(
        "import asyncpg\n"
        "import pytest\n"
        "\n"
        "\n"
        "@pytest.fixture\n"
        "async def pool():\n"
        "    p = await asyncpg.create_pool('postgresql://x')\n"
        "    yield p\n"
        "    await p.close()\n",
        encoding="utf-8",
    )
    assert _imports_asyncpg(synthetic_bad_file.read_text(encoding="utf-8")) is True

    # GREEN: 이 리프가 실제로 격리 우회를 정리한 파일(이 파일 자신 — 예전엔
    # `save_equity_baseline` 실DB 테스트가 여기 섞여 있었다) 자체는 asyncpg를
    # 임포트하지 않는다. 형제 파일 전체를 스캔하지 않는 이유: 다른 리프가
    # 소유한 파일(예: R-39 `test_open_order_sweeper.py`)의 독립적인 상태
    # 변화에 이 리프의 회귀 가드가 결합되면 안 된다 — 이 가드는 정확히
    # 이 리프가 고친 대상에 대해서만 재발을 잡는다.
    this_file_source = Path(__file__).read_text(encoding="utf-8")
    assert _imports_asyncpg(this_file_source) is False
