"""tests/support/db.py::effective_worker_id -- 자식 pytest가 부모 xdist 워커 DB를 지우지 않게 한다.

배경: full CI 57ffb7fb에서 xdist 워커 gw3 안의 자식 `pytest`(KIS 게이트-적색 증명)가
`PYTEST_XDIST_WORKER=gw3`을 물려받아 부모의 `aios_test_ci_full_gw3`를 DROP/CREATE했고,
그 워커의 테스트 287건이 연결 끊김/데이터베이스 없음으로 적색이 됐다.
"""

from __future__ import annotations

from tests.support.db import (
    WORKER_OWNER_PID_ENV,
    effective_worker_id,
    session_database_url,
    worker_owner_marker,
)

_TEMPLATE = "postgresql://u:p@localhost:5432/aios_test_ci_full"


def test_master_without_xdist_keeps_master() -> None:
    assert effective_worker_id({}, 100) == "master"


def test_xdist_worker_first_import_keeps_its_worker_id() -> None:
    assert effective_worker_id({"PYTEST_XDIST_WORKER": "gw3"}, 100) == "gw3"


def test_xdist_worker_reimport_in_same_process_keeps_its_worker_id() -> None:
    env = {"PYTEST_XDIST_WORKER": "gw3", WORKER_OWNER_PID_ENV: worker_owner_marker(100, "gw3")}
    assert effective_worker_id(env, 100) == "gw3"


def test_child_pytest_inheriting_worker_env_gets_a_distinct_id() -> None:
    env = {"PYTEST_XDIST_WORKER": "gw3", WORKER_OWNER_PID_ENV: worker_owner_marker(100, "gw3")}
    child = effective_worker_id(env, 200)
    assert child != "gw3"
    assert session_database_url(_TEMPLATE, child) != session_database_url(_TEMPLATE, "gw3")


def test_two_children_of_the_same_worker_do_not_collide() -> None:
    env = {"PYTEST_XDIST_WORKER": "gw3", WORKER_OWNER_PID_ENV: worker_owner_marker(100, "gw3")}
    assert effective_worker_id(env, 200) != effective_worker_id(env, 201)


def test_child_database_name_stays_within_the_name_rule() -> None:
    env = {"PYTEST_XDIST_WORKER": "gw12", WORKER_OWNER_PID_ENV: worker_owner_marker(100, "gw12")}
    # pid가 7자리여도 `_NAME_RE`(소문자·숫자·밑줄 40자)를 넘지 않아야 한다.
    session_database_url(_TEMPLATE, effective_worker_id(env, 9_999_999))


def test_real_xdist_worker_under_localci_master_keeps_plain_gw_id() -> None:
    """local_ci는 마스터 pytest에 PYTEST_XDIST_WORKER=localci를 심는다. 그 아래의 진짜 xdist
    워커(gwN)는 자식이 아니므로 `{템플릿}_gwN`을 그대로 써야 한다 -- full CI의
    replay_verify가 그 이름으로 DB를 다시 읽는다."""
    env = {"PYTEST_XDIST_WORKER": "gw2", WORKER_OWNER_PID_ENV: worker_owner_marker(50, "localci")}
    assert effective_worker_id(env, 60) == "gw2"


def test_child_of_localci_master_gets_a_distinct_id() -> None:
    """commit 모드(직렬, localci)에서도 자식 pytest가 부모의 `localci` DB를 지우면 안 된다."""
    env = {
        "PYTEST_XDIST_WORKER": "localci",
        WORKER_OWNER_PID_ENV: worker_owner_marker(50, "localci"),
    }
    assert effective_worker_id(env, 60) != "localci"
    assert effective_worker_id(env, 50) == "localci"
