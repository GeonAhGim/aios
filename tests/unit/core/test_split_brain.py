import asyncio

from src.core.safety.split_brain import Diagnosis, SplitBrainDiagnostics


def _diag(**overrides):
    defaults = dict(
        entry_confirm_seconds=0.05, recovery_confirm_seconds=0.1, check_timeout_seconds=0.05
    )
    defaults.update(overrides)
    return SplitBrainDiagnostics(**defaults)


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
    diag = _diag(entry_confirm_seconds=0.05)
    await diag.diagnose(check_exchange=_fail, check_db=_ok, main_process_ok_raw=True)
    await asyncio.sleep(0.06)
    result = await diag.diagnose(check_exchange=_fail, check_db=_ok, main_process_ok_raw=True)
    assert result.exchange_ok is False
    assert result.diagnosis == Diagnosis.APPLY_WATCHDOG_DECISION


async def test_db_isolated_failure_diagnosis():
    diag = _diag(entry_confirm_seconds=0.05)
    await diag.diagnose(check_exchange=_ok, check_db=_fail, main_process_ok_raw=True)
    await asyncio.sleep(0.06)
    result = await diag.diagnose(check_exchange=_ok, check_db=_fail, main_process_ok_raw=True)

    assert result.db_ok is False
    assert result.exchange_ok is True
    assert result.main_process_ok is True
    assert result.diagnosis == Diagnosis.DB_ISOLATED_FAILURE


async def test_recovery_requires_longer_sustained_success():
    diag = _diag(entry_confirm_seconds=0.02, recovery_confirm_seconds=0.1)
    await diag.diagnose(check_exchange=_fail, check_db=_ok, main_process_ok_raw=True)
    await asyncio.sleep(0.03)
    confirmed_bad = await diag.diagnose(
        check_exchange=_fail, check_db=_ok, main_process_ok_raw=True
    )
    assert confirmed_bad.exchange_ok is False

    # 회복 신호가 왔지만 recovery_confirm_seconds(0.1s)가 아직 안 지남
    still_bad = await diag.diagnose(check_exchange=_ok, check_db=_ok, main_process_ok_raw=True)
    assert still_bad.exchange_ok is False

    await asyncio.sleep(0.11)
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
