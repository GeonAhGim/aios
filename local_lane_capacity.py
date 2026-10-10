"""Six-slot local lane policy; shared admission lock includes direct triage."""
from __future__ import annotations

import copy
import datetime as dt
import json
from functools import wraps

import task_store

TARGETS = {"claude-local": 3, "local-qa": 2, "local-review": 1}
SLOTS = 6
COOLDOWN = dt.timedelta(minutes=30)
CANARY_SIZE = 1


def decide(current, day, recent, now, disabled_at=None):
    """24h safety wins; 3h recovery needs five samples and a 60% success rate.

    A burst means >=3 failures and >40% failures in the last three hours.
    The 50..60% band retains existing capacity, never reactivates it.
    """
    done, failed, _ = day or (0, 0, {})
    rd, rf, _ = recent or (0, 0, {})
    unsafe = (done + failed > 0 and done / (done + failed) < .5)
    burst = rf >= 3 and rf / (rd + rf) > .4
    if unsafe or burst:
        return 0, "24h safety downgrade" if unsafe else "3h failure burst"
    if current:
        return current, "hold"
    if disabled_at and now - disabled_at < COOLDOWN:
        return 0, "recovery cooldown"
    if rd + rf >= 5 and rd / (rd + rf) >= .6:
        return 1, "3h recovery"
    if disabled_at and now - disabled_at >= COOLDOWN:
        return CANARY_SIZE, "cooldown canary"
    return 0, "insufficient 3h recovery"


def _tasks_since(tasks, started_at):
    """Keep canary measurements from treating pre-canary history as a result."""
    if not started_at or not isinstance(tasks, dict):
        return tasks
    filtered = {}
    for key, task in tasks.items():
        try:
            updated = dt.datetime.fromisoformat(str(task.get("updated_at", "")))
        except (AttributeError, TypeError, ValueError):
            continue
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=dt.timezone.utc)
        if updated >= started_at:
            filtered[key] = task
    return filtered


def governed_config(config, tasks, now, state_path, measure):
    result = copy.deepcopy(config)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    for lane, target in TARGETS.items():
        pool = result.get("pools", {}).get(lane)
        if pool is None:
            continue
        previous = state.get(lane, {})
        if not isinstance(previous, dict):
            previous = {}
        current = min(target, int(previous.get("size", pool.get("size", 0))))
        try:
            disabled = dt.datetime.fromisoformat(previous["disabled_at"])
            if disabled.tzinfo is None:
                disabled = disabled.replace(tzinfo=dt.timezone.utc)
        except (KeyError, TypeError, ValueError):
            disabled = None
        canary = bool(previous.get("canary"))
        try:
            canary_at = dt.datetime.fromisoformat(previous["canary_at"])
            if canary_at.tzinfo is None:
                canary_at = canary_at.replace(tzinfo=dt.timezone.utc)
        except (KeyError, TypeError, ValueError):
            canary_at = None
        measured_tasks = _tasks_since(tasks, canary_at) if canary else tasks
        day = measure(measured_tasks, now, lane, 24)
        recent = measure(measured_tasks, now, lane, 3)
        size, reason = decide(current, day, recent, now, disabled)
        if canary:
            rd, rf, _ = recent or (0, 0, {})
            if rd + rf == 0:
                size, reason = CANARY_SIZE, "canary pending"
            elif not size:
                canary = False
                disabled = now
                canary_at = None
                reason = "canary failed"
            elif rd > 0 and rf == 0:
                size, canary, disabled, canary_at, reason = (
                    target, False, None, None, "canary passed"
                )
            else:
                size, reason = CANARY_SIZE, "canary pending"
        elif reason == "cooldown canary":
            size, canary = CANARY_SIZE, True
            canary_at = now
        else:
            canary_at = None
        if not size and current:
            disabled = now
            canary = False
            canary_at = None
        pool["size"] = CANARY_SIZE if canary else (target if size else 0)
        pool["cap_burst"] = 0
        state[lane] = {"size": pool["size"], "reason": reason,
                       "disabled_at": disabled.isoformat() if disabled else None,
                       "canary": canary,
                       "canary_at": canary_at.isoformat() if canary_at else None}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(state_path)
    return result


def admission_lock(function):
    """Serialize spawn and direct triage admission, including the triage call."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        import orchestrator as o
        o.LOGS.mkdir(parents=True, exist_ok=True)
        try:
            with task_store._file_lock(o.LOGS / "local_capacity_admission", timeout=.01):
                return function(*args, **kwargs)
        except TimeoutError:
            return {"ran": False, "reason": "local admission busy"}
    return wrapped


def running_count(tasks, workers=()):
    names = {str(t.get("worker") or f"task-{key}") for key, t in tasks.items()
             if t.get("status") == "in_progress" and
             (t.get("role") in TARGETS or t.get("engine") == "claude-local")}
    names.update(w for w in workers if any(w.startswith(lane + "-") for lane in TARGETS))
    return len(names)


@admission_lock
def run_triage(**kwargs):
    import local_triage
    import orchestrator as o
    if running_count(o.tasks(), set(o._running) | set(o.live_runner_tasks().values())) >= SLOTS:
        return {"ran": False, "reason": "all six local slots occupied"}
    return local_triage.run_cycle(**kwargs)
