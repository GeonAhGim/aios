"""task-1986(esc-ci-a63996275e23) 후속 — per-test 타임아웃(task-645,
pyproject.toml `timeout_method = "thread"`)이 실제로 어디까지 보호하는지
서브프로세스로 증명한다.

**배경**: task-645는 "테스트 하나가 무한 대기해도 pytest 프로세스 자체는
살아있으므로, per-test 타임아웃을 걸어 그 하나만 실패시키고 스택 트레이스를
남긴다"고 주장했다(pyproject.toml 주석). task-1986 조사 중 이 주장을 실측
재현했더니 **사실이 아니었다** — `pytest_timeout.py`의 "thread" 방식
(`timeout_timer`)은 스택을 덤프한 뒤 `os._exit(1)`을 호출해 **프로세스 전체를
죽인다**(신호 기반 "signal" 방식만 해당 테스트 하나에 예외를 던져 프로세스를
살려 두는데, SIGALRM이 없는 Windows에서는 쓸 수 없다 — 이 저장소의 실제 CI
러너(`C:\\aios\\pm\\local_ci.py`)가 Windows에서 돈다).

그래도 task-645가 실제로 얻은 것은 있다: 죽기 직전 **어느 테스트의 어느
줄에서 멈췄는지 스택 트레이스로 남긴다** — 원래 3300s 상한이 죽였을 때
"무엇이 원인인지조차 모른다"였던 문제(esc-ci-a63996275e23)는 이걸로
해결된다. 이 테스트는 그 진단 능력이 실제로 작동함을 고정하고, 동시에
"나머지 테스트는 계속 돈다"는 아직 사실이 아님을 함께 고정해 향후 누군가
주석만 보고 잘못된 안전성을 다시 주장하지 않게 한다(I-10: "배선돼 있다"가
아니라 "무엇을 보장하고 무엇을 보장하지 않는지 증명됨"이어야 한다).

프로세스 전체가 죽지 않고 hang 1건만 실패시키려면 pytest-xdist로 테스트를
여러 워커 프로세스에 분산해(워커 하나가 os._exit해도 나머지 워커는 살아
있다) 그 워커의 크래시만 감수하는 구조가 필요하다 — `tests/support/db.py`의
워커별 DB 격리(PLT-36, d0b948a)가 이미 이를 위해 준비돼 있지만
`C:\\aios\\pm\\local_ci.py`의 pytest 단계는 아직 `-n`을 쓰지 않는다. task-1986
조사에서 `pytest tests/ -n 4`를 실측했더니 전체 벽시계 시간은 크게
줄었지만(§note) 직렬 실행에서는 통과하던 테스트 6개가 새로 실패했다(격리
가정이 깨지는 지점 존재) — 그 격리 결함을 먼저 고치기 전에는 `-n`을 CI에
배선하면 안 된다(단언 약화 금지: 타임아웃 회피가 새로운 거짓-빨강을 만들면
순손실이다).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HANGING_MODULE = textwrap.dedent(
    """
    import time
    import pytest

    @pytest.mark.timeout(1)
    def test_intentionally_hangs_past_its_timeout():
        time.sleep(5)

    def test_sibling_would_run_next_if_the_process_survived():
        assert True
    """
)


def _run_synthetic_hang(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    module = tmp_path / "test_synthetic_hang.py"
    module.write_text(_HANGING_MODULE, encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-v",
            "--timeout-method=thread",
            str(module),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_hang_past_per_test_timeout_is_attributed_to_the_offending_test(
    tmp_path: Path,
) -> None:
    """진단 능력(task-645가 실제로 고친 부분) — 죽기 전에 어느 테스트·어느
    줄에서 멈췄는지 스택 트레이스에 정확히 남는다."""
    result = _run_synthetic_hang(tmp_path)

    assert result.returncode != 0
    assert "Timeout" in result.stdout
    assert "test_intentionally_hangs_past_its_timeout" in result.stdout
    assert "time.sleep(5)" in result.stdout


def test_hang_past_per_test_timeout_still_kills_the_whole_process(
    tmp_path: Path,
) -> None:
    """현재 한계(아직 고치지 못한 부분) — "thread" 방식은 `os._exit(1)`로
    프로세스 전체를 끝내므로, 같은 실행의 다음 테스트는 시작조차 못 한다.
    이 단언이 언젠가 깨진다면(=다음 테스트가 실행됨) 그건 회귀가 아니라
    개선이다 — 그때는 이 테스트와 위 pyproject.toml 주석을 함께 갱신할 것."""
    result = _run_synthetic_hang(tmp_path)

    assert "test_sibling_would_run_next_if_the_process_survived" not in result.stdout
    assert "1 passed" not in result.stdout


def test_non_numeric_timeout_marker_value_is_rejected_not_silently_ignored(
    tmp_path: Path,
) -> None:
    """부정 케이스 — `@pytest.mark.timeout("abc")`처럼 불변식(타임아웃 값은
    숫자여야 한다)을 위반한 입력은 pytest_timeout이 `_validate_timeout`에서
    명시적으로 거부한다(INTERNALERROR, exit code 3) — 조용히 무시하고
    타임아웃 없이 테스트를 통과시키는 fail-open이 아니다."""
    module = tmp_path / "test_bad_marker_value.py"
    module.write_text(
        textwrap.dedent(
            """
            import pytest

            @pytest.mark.timeout("abc")
            def test_would_never_time_out_if_silently_ignored():
                assert True
            """
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-v",
            "--timeout-method=thread",
            str(module),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 3
    assert "INTERNALERROR" in result.stdout
    assert "Invalid timeout" in result.stdout
    assert "1 passed" not in result.stdout


def test_negative_timeout_marker_value_disables_enforcement_instead_of_erroring(
    tmp_path: Path,
) -> None:
    """부정 케이스 — 음수 타임아웃(`@pytest.mark.timeout(-1)`)은
    `_validate_timeout`이 숫자형이라는 이유만으로 통과시켜 타임아웃이 꺼진
    채로 조용히 성공한다. `test_non_numeric_timeout_marker_value_is_rejected_*`
    와 대비해 "숫자형이면 부호는 검증하지 않는다"는 이 플러그인의 실제 계약을
    고정한다 — 음수 타임아웃을 실수로 설정해도 CI가 이를 걸러주지 않는다는
    한계를 문서화된 가정이 아니라 관측된 사실로 남긴다."""
    module = tmp_path / "test_negative_marker_value.py"
    module.write_text(
        textwrap.dedent(
            """
            import pytest

            @pytest.mark.timeout(-1)
            def test_passes_because_negative_timeout_is_not_rejected():
                assert True
            """
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-v",
            "--timeout-method=thread",
            str(module),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert "1 passed" in result.stdout
    assert "INTERNALERROR" not in result.stdout


def test_signal_timeout_method_is_rejected_on_windows(tmp_path: Path) -> None:
    """부정 케이스 — 이 파일의 docstring이 주장하는 "SIGALRM이 없는
    Windows에서는 signal 방식을 쓸 수 없다"를 실측으로 고정한다.
    `--timeout-method=signal`을 Windows worktree에서 강제하면 조용히 무시되고
    thread 방식으로 폴백하는 것이 아니라 `AttributeError`로 INTERNALERROR가
    나며 죽는다 — 이 저장소가 `timeout_method = "thread"`를 고정한 이유가
    "선호"가 아니라 "Windows에서는 그 외에는 동작하지 않기 때문"임을 증명한다.

    로컬 worktree(Windows)와 달리 `.github/workflows/quality.yml`의 `verify`
    잡은 `ubuntu-latest`에서 돈다 — SIGALRM이 있는 POSIX에서는 같은
    `--timeout-method=signal`이 INTERNALERROR 없이 정상 동작한다(task-11265:
    이 테스트가 플랫폼과 무관하게 `sys.platform == "win32"`를 강제해 GH
    Actions에서 항상 FAIL했던 회귀). skip/skipif 대신 플랫폼별 기대 동작을
    직접 단언해 `check_code_ratchets.py`의 `skip_xfail` 기준선을 건드리지
    않으면서도 두 플랫폼 모두에서 실측 그대로를 고정한다."""
    module = tmp_path / "test_signal_method.py"
    module.write_text(
        textwrap.dedent(
            """
            import time
            import pytest

            @pytest.mark.timeout(1)
            def test_would_hang():
                time.sleep(2)
            """
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-v",
            "--timeout-method=signal",
            str(module),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if sys.platform == "win32":
        assert result.returncode == 3
        assert "INTERNALERROR" in result.stdout
        assert "SIGALRM" in result.stdout
    else:
        assert result.returncode == 1
        assert "INTERNALERROR" not in result.stdout
        assert "Timeout" in result.stdout


def test_missing_synthetic_module_path_fails_closed_instead_of_reporting_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입 — `_run_synthetic_hang`이 대상 모듈을 실제로 쓰지 못하는
    상황(디스크 쓰기 실패를 모사해 `Path.write_text`가 예외를 던지도록
    monkeypatch)에서도 예외가 조용히 삼켜져 "성공"으로 보고되지 않고
    그대로 전파되는지 확인한다 — 스캐폴딩(synthetic hang 모듈 생성)이
    깨지면 진단 테스트 자체가 거짓-초록으로 통과해서는 안 된다."""

    def _boom(self: Path, data: str, encoding: str) -> None:
        raise OSError("simulated disk failure while writing synthetic hang module")

    monkeypatch.setattr(Path, "write_text", _boom)

    with pytest.raises(OSError, match="simulated disk failure"):
        _run_synthetic_hang(tmp_path)
