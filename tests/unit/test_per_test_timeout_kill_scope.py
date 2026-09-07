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
            sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-v",
            "--timeout-method=thread", str(module),
        ],
        capture_output=True, text=True, timeout=30,
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
