"""Capacity safety and recovery boundaries, isolated from fleet state."""

import datetime as dt
import json
from pathlib import Path

import healthcheck
import orchestrator as o
import pytest
import yaml

import local_lane_capacity as cap

NOW = dt.datetime(2026, 9, 20, 12, tzinfo=dt.timezone.utc)


@pytest.mark.parametrize(
    "current,day,recent,expected",
    [
        (0, (8, 2, {}), (3, 2, {}), 1),
        (0, (8, 2, {}), (3, 1, {}), 0),
        (0, (8, 2, {}), (2, 3, {}), 0),
        (0, (4, 6, {}), (5, 0, {}), 0),
        (2, (4, 6, {}), (5, 0, {}), 0),
        (2, (20, 3, {}), (1, 3, {}), 0),
        (2, (5, 5, {}), (1, 1, {}), 2),
        (0, None, None, 0),
    ],
)
def test_policy(current, day, recent, expected):
    assert cap.decide(current, day, recent, NOW)[0] == expected


def test_cooldown_persists_across_polls(tmp_path):
    def bad_measure(*args):
        return (1, 4, {})

    def good_measure(*args):
        return (5, 0, {})

    cfg = {"pools": {k: {"size": v, "cap_burst": 99} for k, v in cap.TARGETS.items()}}
    path = tmp_path / "capacity.json"
    first = cap.governed_config(cfg, {}, NOW, path, bad_measure)
    assert sum(p["size"] for p in first["pools"].values()) == 0
    second = cap.governed_config(cfg, {}, NOW + dt.timedelta(minutes=29), path, good_measure)
    assert sum(p["size"] for p in second["pools"].values()) == 0
    third = cap.governed_config(cfg, {}, NOW + dt.timedelta(minutes=30), path, good_measure)
    assert [p["size"] for p in third["pools"].values()] == [3, 2, 1]
    assert all(p["cap_burst"] == 0 for p in third["pools"].values())


def test_cooldown_canary_breaks_zero_capacity_deadlock(tmp_path):
    def bad_measure(*args):
        return (1, 4, {})

    def no_sample_measure(*args):
        return None

    cfg = {"pools": {k: {"size": v, "cap_burst": 99} for k, v in cap.TARGETS.items()}}
    path = tmp_path / "capacity.json"
    cap.governed_config(cfg, {}, NOW, path, bad_measure)
    canary = cap.governed_config(cfg, {}, NOW + dt.timedelta(minutes=30), path, no_sample_measure)
    assert [canary["pools"][lane]["size"] for lane in cap.TARGETS] == [1, 1, 1]
    state = json.loads(path.read_text(encoding="utf-8"))
    assert all(state[lane]["canary"] for lane in cap.TARGETS)

    def success(*args):
        return (1, 0, {})

    promoted = cap.governed_config(cfg, {}, NOW + dt.timedelta(minutes=31), path, success)
    assert [promoted["pools"][lane]["size"] for lane in cap.TARGETS] == [3, 2, 1]
    state = json.loads(path.read_text(encoding="utf-8"))
    assert all(not state[lane]["canary"] for lane in cap.TARGETS)


def test_canary_ignores_pre_canary_safety_history(tmp_path):
    def bad_measure(*args):
        return (1, 4, {})

    def no_sample_measure(*args):
        return None

    cfg = {"pools": {k: {"size": v} for k, v in cap.TARGETS.items()}}
    path = tmp_path / "capacity.json"
    cap.governed_config(cfg, {}, NOW, path, bad_measure)
    cap.governed_config(cfg, {}, NOW + dt.timedelta(minutes=30), path, no_sample_measure)

    old_failure = {"old": {"updated_at": NOW.isoformat()}}

    def historical_failure(tasks, *args):
        return (1, 4, {}) if tasks else None

    pending = cap.governed_config(
        cfg, old_failure, NOW + dt.timedelta(minutes=31), path, historical_failure
    )
    assert [pending["pools"][lane]["size"] for lane in cap.TARGETS] == [1, 1, 1]
    assert all(
        item["reason"] == "canary pending"
        for item in json.loads(path.read_text(encoding="utf-8")).values()
    )


def test_canary_failure_returns_to_zero(tmp_path):
    def bad_measure(*args):
        return (1, 4, {})

    def no_sample_measure(*args):
        return None

    cfg = {"pools": {k: {"size": v} for k, v in cap.TARGETS.items()}}
    path = tmp_path / "capacity.json"
    cap.governed_config(cfg, {}, NOW, path, bad_measure)
    cap.governed_config(cfg, {}, NOW + dt.timedelta(minutes=30), path, no_sample_measure)
    failed = cap.governed_config(cfg, {}, NOW + dt.timedelta(minutes=31), path, bad_measure)
    assert all(pool["size"] == 0 for pool in failed["pools"].values())
    assert all(
        pool["reason"] == "canary failed"
        for pool in json.loads(path.read_text(encoding="utf-8")).values()
    )


def test_worker_pools_stopped_diagnostic_contains_reason():
    cfg = {
        "pools": {
            "backend": {"size": 1, "engine": "claude"},
            **{lane: {"size": 0, "engine": "claude-local"} for lane in cap.TARGETS},
        }
    }
    tasks = {1: {"id": 1, "role": "backend", "status": "assigned", "depends_on": []}}
    diagnosis = o.worker_pools_stopped(cfg, tasks)
    assert diagnosis["ready_tasks"] == 1
    assert diagnosis["in_progress"] == 0
    assert diagnosis["local_capacity"] == 0
    assert "governed local capacity is zero" in diagnosis["reason"]
    finding = healthcheck.check_worker_pools_stopped(tasks, cfg)
    assert finding[0]["code"] == "worker_pools_stopped"


def test_three_hour_window_excludes_old_successes():
    tasks = {
        i: {
            "status": "done",
            "engine": "claude-local",
            "stage": "qa",
            "updated_at": (NOW - dt.timedelta(hours=4)).isoformat(),
        }
        for i in range(6)
    }
    assert healthcheck.measure_local_lane_merge_rate(tasks, NOW, "local-qa", 3) is None
    assert healthcheck.measure_local_lane_merge_rate(tasks, NOW, "local-qa", 24)[0] == 6


def test_running_count_deduplicates_and_counts_legacy_overcapacity():
    tasks = {
        i: {"status": "in_progress", "role": "claude-local", "worker": f"claude-local-{i}"}
        for i in range(1, 8)
    }
    assert cap.running_count(tasks, ["claude-local-1"]) == 7


def test_triage_cannot_bypass_full_capacity(monkeypatch):
    tasks = {
        i: {"status": "in_progress", "role": "claude-local", "worker": f"claude-local-{i}"}
        for i in range(6)
    }
    monkeypatch.setattr(o, "tasks", lambda: tasks)
    monkeypatch.setattr(o, "live_runner_tasks", lambda: {})
    monkeypatch.setattr(o, "_running", {})
    assert cap.run_triage(capped=True, force=True)["ran"] is False


def test_triage_admission_excludes_concurrent_spawn(monkeypatch):
    import task_store

    with task_store._file_lock(o.LOGS / "local_capacity_admission"):
        assert cap.run_triage()["reason"] == "local admission busy"


def test_config_targets_and_disabled_external_pools():
    cfg = yaml.safe_load((Path(__file__).parents[1] / "pools.yaml").read_text(encoding="utf-8"))
    pools = cfg["pools"]
    assert [pools[k]["size"] for k in cap.TARGETS] == [3, 0, 1]
    assert sum(cap.TARGETS.values()) == 6
    assert all(p.get("cap_burst", 0) == 0 for k, p in pools.items() if k in cap.TARGETS)
    assert all(
        p["size"] == 0 for p in pools.values() if p.get("engine") in {"codex", "cursor", "copilot"}
    )


@pytest.mark.parametrize("forbidden", [None, "S", "migration", "refactor-wide"])
@pytest.mark.parametrize("occupied,expected", [(0, 3), (5, 1), (6, 0), (7, 0)])
def test_spawn_enforces_shared_six_slot_ceiling(
    monkeypatch, tmp_path, occupied, expected, forbidden
):
    tasks = {
        i: {"id": i, "status": "in_progress", "role": "local-review", "worker": f"local-review-{i}"}
        for i in range(occupied)
    }
    for i in range(10, 20):
        tasks[i] = {
            "id": i,
            "status": "assigned",
            "role": "claude-local",
            "files": ["tests/safe.py"],
            "title": "capacity fixture",
            "spec": "small fix",
            "depends_on": [],
            "priority": 1,
            "worker": "",
        }
    for task in tasks.values():
        if task["status"] == "assigned":
            if forbidden == "S":
                task["tier"] = "S"
            elif forbidden == "migration":
                task["spec"] = "\ub9c8\uc774\uadf8\ub808\uc774\uc158"
            elif forbidden == "refactor-wide":
                task["files"] = [f"module{i}.py" for i in range(20)]
    monkeypatch.setattr(o, "tasks", lambda: tasks)
    monkeypatch.setattr(o, "_running", {})
    monkeypatch.setattr(o, "live_runner_tasks", dict)
    monkeypatch.setattr(o, "load_pool_overrides", lambda: {"claude-local": 99})
    monkeypatch.setattr(o, "load_quarantined_slots", dict)
    monkeypatch.setattr(o, "load_tiers", dict)
    monkeypatch.setattr(o, "classify_tier", lambda *a: "L")
    monkeypatch.setattr(o, "paused", lambda: False)
    monkeypatch.setattr(o, "_claude_capped", lambda *a: True)
    monkeypatch.setattr(o, "external_engine_available", lambda *a: True)
    monkeypatch.setattr(o, "free_ram_gb", lambda: 99)
    monkeypatch.setattr(o, "_spawn_guard_reject", lambda *a: None)
    monkeypatch.setattr(o, "save", lambda *a: None)
    for name in (
        "auto_route_external",
        "auto_route_local_qa_review",
        "auto_route_capped_qa_review",
    ):
        if hasattr(o, name):
            monkeypatch.setattr(o, name, lambda *a: None)
    calls = []
    monkeypatch.setattr(o.subprocess, "Popen", lambda *a, **kw: calls.append(a))
    cfg = {
        "repos": {"backend": {"path": str(tmp_path), "prompt": "fixture.md"}},
        "pools": {
            "claude-local": {
                "size": 3,
                "cap_burst": 99,
                "model": "local",
                "engine": "claude-local",
                "repo": "backend",
            }
        },
    }
    o.spawn(cfg)
    assert len(calls) == (0 if forbidden else expected)


def test_disabled_qa_requires_its_own_recovery(monkeypatch, tmp_path):
    cfg = {"pools": {lane: {"size": 0} for lane in cap.TARGETS}}

    def measure(tasks, now, lane, hours):
        return (0, 10, {}) if lane == "local-qa" else (5, 0, {})

    result = cap.governed_config(cfg, {}, NOW, tmp_path / "capacity.json", measure)
    assert [result["pools"][lane]["size"] for lane in cap.TARGETS] == [3, 0, 1]
