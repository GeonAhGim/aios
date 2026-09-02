import asyncio

from src.core.safety.split_brain import Diagnosis, SplitBrainDiagnostics


class _FakeClock:
    """실시간 sleep 대신 명시적으로 전진시키는 monotonic 시계 대역.

    CI 부하로 인한 실시간 sleep 오차가 entry/recovery 판정 경계를 흔드는
    것을 막기 위해 diagnose() 호출 사이의 "경과 시간"을 결정론적으로 만든다.
    """

    def __init__(self) -> None:
        self._now = 0.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _diag(**overrides):
    defaults = dict(
        entry_confirm_seconds=0.05, recovery_confirm_seconds=0.1, check_timeout_seconds=0.05
    )
    defaults.update(overrides)
    return SplitBrainDiagnostics(**defaults)


def _diag_with_clock(**overrides):
    clock = _FakeClock()
    diag = _diag(clock=clock, **overrides)
    return diag, clock


async def _ok():
    return True


async def _fail():
    return False


async def _raises():
    raise ConnectionError("boom")


async def _hangs():
    await asyncio.sleep(10)
    return True


async def test_all_healthy_is_normal():
    diag = _diag()
    result = await diag.diagnose(check_exchange=_ok, check_db=_ok, main_process_ok_raw=True)
    assert result.diagnosis == Diagnosis.NORMAL


async def test_momentary_failure_does_not_immediately_flip_confirmed_state():
    diag = _diag(entry_confirm_seconds=1.0)
    result = await diag.diagnose(check_exchange=_fail, check_db=_ok, main_process_ok_raw=True)
    assert result.exchange_ok is True  # 1초 안 지났으므로 아직 confirmed OK


async def test_sustained_failure_flips_after_entry_duration():
    diag, clock = _diag_with_clock(entry_confirm_seconds=0.05)
    await diag.diagnose(check_exchange=_fail, check_db=_ok, main_process_ok_raw=True)

    # 임계값(0.05s) 직전 — 아직 전이하면 안 된다(negative).
    clock.advance(0.049)
    not_yet = await diag.diagnose(check_exchange=_fail, check_db=_ok, main_process_ok_raw=True)
    assert not_yet.exchange_ok is True
    assert not_yet.diagnosis == Diagnosis.NORMAL

    # 임계값을 넘기면 전이한다.
    clock.advance(0.01)
    result = await diag.diagnose(check_exchange=_fail, check_db=_ok, main_process_ok_raw=True)
    assert result.exchange_ok is False
    assert result.diagnosis == Diagnosis.APPLY_WATCHDOG_DECISION


async def test_db_isolated_failure_diagnosis():
    diag, clock = _diag_with_clock(entry_confirm_seconds=0.05)
    await diag.diagnose(check_exchange=_ok, check_db=_fail, main_process_ok_raw=True)
    clock.advance(0.06)
    result = await diag.diagnose(check_exchange=_ok, check_db=_fail, main_process_ok_raw=True)

    assert result.db_ok is False
    assert result.exchange_ok is True
    assert result.main_process_ok is True
    assert result.diagnosis == Diagnosis.DB_ISOLATED_FAILURE


async def test_recovery_requires_longer_sustained_success():
    diag, clock = _diag_with_clock(entry_confirm_seconds=0.02, recovery_confirm_seconds=0.1)
    await diag.diagnose(check_exchange=_fail, check_db=_ok, main_process_ok_raw=True)
    clock.advance(0.03)
    confirmed_bad = await diag.diagnose(
        check_exchange=_fail, check_db=_ok, main_process_ok_raw=True
    )
    assert confirmed_bad.exchange_ok is False

    # 회복 신호가 왔지만 recovery_confirm_seconds(0.1s)가 아직 안 지남
    still_bad = await diag.diagnose(check_exchange=_ok, check_db=_ok, main_process_ok_raw=True)
    assert still_bad.exchange_ok is False

    clock.advance(0.11)
    recovered = await diag.diagnose(check_exchange=_ok, check_db=_ok, main_process_ok_raw=True)
    assert recovered.exchange_ok is True


async def test_check_exception_treated_as_failure():
    diag = _diag(entry_confirm_seconds=0.0)
    result = await diag.diagnose(check_exchange=_raises, check_db=_ok, main_process_ok_raw=True)
    assert result.exchange_ok is False


async def test_check_timeout_treated_as_failure():
    diag = _diag(entry_confirm_seconds=0.0, check_timeout_seconds=0.05)
    result = await diag.diagnose(check_exchange=_hangs, check_db=_ok, main_process_ok_raw=True)
    assert result.exchange_ok is False


async def test_main_process_unresponsive_triggers_apply_watchdog_decision():
    diag = _diag(entry_confirm_seconds=0.0)
    result = await diag.diagnose(check_exchange=_ok, check_db=_ok, main_process_ok_raw=False)
    assert result.diagnosis == Diagnosis.APPLY_WATCHDOG_DECISION
