"""L4-11 — error_taxonomy 단위 테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§9 L4-11
DoD: 429+Retry-After, 500/502/503, 401/403, 비JSON, 미지 코드 →
UNKNOWN_RESPONSE(retryable=False).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path
from time import perf_counter

import pytest

from src.core.exceptions import ExchangeAPIError
from src.exchanges.common.error_taxonomy import (
    ExchangeError,
    ExchangeErrorKind,
    classify_http,
    is_retryable,
)


def test_429_with_retry_after_classifies_rate_limited() -> None:
    assert classify_http(429, "3") == ExchangeErrorKind.RATE_LIMITED


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_5xx_classifies_server_error(status: int) -> None:
    assert classify_http(status, None) == ExchangeErrorKind.SERVER_ERROR


@pytest.mark.parametrize("status", [401, 403])
def test_401_403_classifies_auth(status: int) -> None:
    assert classify_http(status, None) == ExchangeErrorKind.AUTH


def test_unknown_status_returns_none() -> None:
    """비JSON 본문 등으로 호출부가 상태코드를 신뢰할 수 없는 경우와 동일하게,
    표에 없는 상태코드는 None을 반환해 호출부가 UNKNOWN_RESPONSE로
    승격시키도록 강제한다."""
    assert classify_http(200, None) is None
    assert classify_http(999, None) is None


def test_unknown_response_is_not_retryable_fail_closed() -> None:
    """미지 응답(비JSON 본문 포함)은 반드시 retryable=False다 — 잔고 부족 같은
    영구 오류를 UNKNOWN_RESPONSE로 오분류해도 재시도 폭주로 이어지지 않아야
    한다."""
    kind = classify_http(999, None)
    assert kind is None
    err = ExchangeError(ExchangeErrorKind.UNKNOWN_RESPONSE, venue="bitget", http_status=999)
    assert err.retryable is False
    assert err.kind == ExchangeErrorKind.UNKNOWN_RESPONSE
    assert isinstance(err, ExchangeAPIError)


def test_insufficient_funds_is_not_retryable() -> None:
    """알려진 영구 오류(kind가 명시적으로 분류된 경우)도 재시도 대상이
    아니어야 한다 — 미지 코드뿐 아니라 알려진 영구 오류도 fail-closed."""
    err = ExchangeError(ExchangeErrorKind.INSUFFICIENT_FUNDS)
    assert err.retryable is False


@pytest.mark.parametrize(
    "kind",
    [
        ExchangeErrorKind.TRANSIENT_NETWORK,
        ExchangeErrorKind.RATE_LIMITED,
        ExchangeErrorKind.SERVER_ERROR,
    ],
)
def test_known_transient_kinds_are_retryable(kind: ExchangeErrorKind) -> None:
    assert is_retryable(kind) is True


def test_explicit_retryable_override_is_respected() -> None:
    err = ExchangeError(ExchangeErrorKind.SERVER_ERROR, retryable=False, circuit_open=True)
    assert err.retryable is False
    assert err.circuit_open is True


def test_classify_and_error_construction_latency_budget() -> None:
    """`classify_http`/`ExchangeError` 생성은 요청 경로마다 한 번씩 호출되는
    hot path — 로컬 회귀 예산이며 SLO 단언은 아니다(headless worker 지침)."""
    classify_http(429, "3")  # 콜드 스타트 워밍업 — 예산 밖.
    started = perf_counter()
    for i in range(20_000):
        kind = classify_http(500 if i % 2 == 0 else 999, None) or ExchangeErrorKind.UNKNOWN_RESPONSE
        ExchangeError(kind, venue="bitget", http_status=500)
    elapsed = perf_counter() - started
    assert elapsed < 3.0, f"20000 classify+construct cycles took {elapsed:.3f}s (budget 3.0s)"


def test_pytest_gate_turns_red_when_unknown_response_retryable_guard_is_removed(
    tmp_path: Path,
) -> None:
    """실제 fail-closed 테스트가 통과하는 걸 먼저 확인하고, `is_retryable`이
    조회 표를 무시하고 항상 True를 반환하도록 만들면(=미지 응답도 재시도
    가능하다고 오분류하면) pytest가 exit 1로 red가 되는 것까지 증명한다
    (gate/CI red-line regression proof, DEPTH_L4_BR task-456 D2 미달 사유 해소)."""
    test_copy = tmp_path / "test_error_taxonomy_copy.py"
    test_copy.write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    command = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "-c", str(config), "--confcutdir", str(tmp_path),
        f"{test_copy}::test_unknown_response_is_not_retryable_fail_closed",
    ]
    env = dict(
        os.environ, PYTHONPATH=str(Path.cwd()), PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8"
    )
    baseline = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=60, check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    # 프로덕션 소스 파일은 그대로 두고, 이 자식 프로세스의 모듈 객체만 변조한다.
    (tmp_path / "conftest.py").write_text(
        "import importlib\nfrom pathlib import Path\n"
        "name = 'src.exchanges.common.error_taxonomy'\n"
        "module = importlib.import_module(name)\n"
        "source = Path(module.__file__).read_text(encoding='utf-8')\n"
        "guard = '    return kind in _RETRYABLE_KINDS'\n"
        "assert source.count(guard) == 1\n"
        "mutant = compile(source.replace(guard, '    return True'), module.__file__, 'exec')\n"
        "exec(mutant, module.__dict__)\n",
        encoding="utf-8",
    )
    mutated = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=60, check=False,
    )
    assert mutated.returncode == 1, mutated.stdout + mutated.stderr
    assert "1 failed" in mutated.stdout


def test_many_concurrently_constructed_errors_do_not_leak_state_across_instances() -> None:
    """적대적/다중 인스턴스 증명(D3) — `ExchangeError`는 클래스 수준 공유
    가변 상태가 없어야 한다. 여러 스레드에서 서로 다른 kind/venue로 동시에
    인스턴스를 생성한 뒤, 각 인스턴스가 자신의 값만 갖고 다른 인스턴스와
    뒤섞이지 않았는지 확인한다."""
    kinds = list(ExchangeErrorKind)
    results: list[ExchangeError | None] = [None] * len(kinds) * 50

    def _build(idx: int, kind: ExchangeErrorKind, venue: str) -> None:
        results[idx] = ExchangeError(kind, venue=venue, http_status=idx)

    threads = [
        threading.Thread(target=_build, args=(i, kinds[i % len(kinds)], f"venue-{i}"))
        for i in range(len(results))
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i, err in enumerate(results):
        assert err is not None
        assert err.kind == kinds[i % len(kinds)]
        assert err.venue == f"venue-{i}"
        assert err.http_status == i


def test_classify_http_is_pure_and_replay_safe() -> None:
    """적대적/리플레이 증명(D3) — 동일 입력을 반복(replay)해서 호출해도
    숨은 상태에 의해 결과가 달라지지 않는다(캐시 오염·전역 카운터 없음).
    또한 적대적 `retry_after` 입력(거대 문자열, 비수치 문자열, None 혼합)이
    분류 결과에 영향을 주지 않는지도 함께 확인한다."""
    adversarial_retry_after_values = [None, "3", "not-a-number", "9" * 10_000, ""]
    for _ in range(3):  # replay: 같은 시퀀스를 반복 호출
        for retry_after in adversarial_retry_after_values:
            assert classify_http(429, retry_after) == ExchangeErrorKind.RATE_LIMITED
            assert classify_http(200, retry_after) is None


async def test_concurrent_error_taxonomy_calls_across_asyncio_tasks_are_isolated() -> None:
    """다중 인스턴스 증명(D3, asyncio 버전) — 여러 코루틴이 동시에 서로 다른
    상태코드를 분류/오류 생성해도 서로의 결과를 관측하지 못해야 한다."""

    async def _classify_and_build(status: int) -> ExchangeError:
        kind = classify_http(status, None) or ExchangeErrorKind.UNKNOWN_RESPONSE
        await asyncio.sleep(0)  # 다른 태스크와 인터리빙을 강제한다.
        return ExchangeError(kind, http_status=status)

    statuses = [401, 403, 429, 500, 502, 503, 504, 999]
    results = await asyncio.gather(*(_classify_and_build(s) for s in statuses))
    for status, err in zip(statuses, results, strict=True):
        assert err.http_status == status
        expected_kind = classify_http(status, None) or ExchangeErrorKind.UNKNOWN_RESPONSE
        assert err.kind == expected_kind
