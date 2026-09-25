"""task-4983: `FrozenDict`/`FrozenList` (I-09 nested-container freezing) —
after making both classes generic (`dict[K, V]` / `list[T]`) to clear the
`type: ignore[type-arg]` budget overage, these tests pin down that runtime
behavior is unchanged: mutation still raises, pickling still round-trips via
the constructor, and subscripting (`FrozenDict[str, int]`) still works at
runtime the way `v1.py`'s field validators rely on.
"""

from __future__ import annotations

import pickle
import time

import pytest

from src.foundation.mandates.contracts._frozen_containers import FrozenDict, FrozenList


def test_frozen_dict_rejects_setitem() -> None:
    frozen = FrozenDict[str, int]({"a": 1})
    with pytest.raises(TypeError):
        frozen["a"] = 2


def test_frozen_dict_rejects_update() -> None:
    frozen = FrozenDict[str, int]({"a": 1})
    with pytest.raises(TypeError):
        frozen.update({"b": 2})


def test_frozen_dict_rejects_pop_and_popitem_and_setdefault() -> None:
    frozen = FrozenDict[str, int]({"a": 1})
    with pytest.raises(TypeError):
        frozen.pop("a")
    with pytest.raises(TypeError):
        frozen.popitem()
    with pytest.raises(TypeError):
        frozen.setdefault("b", 2)
    assert dict(frozen) == {"a": 1}


def test_frozen_dict_rejects_ior() -> None:
    """task-4975 QA: `dict.__ior__` (the `|=` operator, PEP 584) was not
    overridden — `frozen |= {...}` mutated the backing dict in place via
    `__ior__` *before* the caller's rebind of the name/attribute could ever
    be observed, so `TypeError` from `_blocked` never fired and the content
    was tampered silently. Reproduced against `RuleHit.evidence` directly in
    `test_contracts_v1.py::test_rule_hit_evidence_dict_rejects_ior_operator`.
    """
    frozen = FrozenDict[str, int]({"a": 1})
    with pytest.raises(TypeError):
        frozen |= {"b": 2}
    assert dict(frozen) == {"a": 1}


def test_frozen_dict_rejects_clear_and_delitem() -> None:
    frozen = FrozenDict[str, int]({"a": 1})
    with pytest.raises(TypeError):
        del frozen["a"]
    with pytest.raises(TypeError):
        frozen.clear()
    assert dict(frozen) == {"a": 1}


def test_frozen_list_rejects_append_extend_insert() -> None:
    frozen = FrozenList[int]([1, 2, 3])
    with pytest.raises(TypeError):
        frozen.append(4)
    with pytest.raises(TypeError):
        frozen.extend([4, 5])
    with pytest.raises(TypeError):
        frozen.insert(0, 4)
    assert list(frozen) == [1, 2, 3]


def test_frozen_list_rejects_setitem_delitem_and_iadd() -> None:
    frozen = FrozenList[int]([1, 2, 3])
    with pytest.raises(TypeError):
        frozen[0] = 9
    with pytest.raises(TypeError):
        del frozen[0]
    with pytest.raises(TypeError):
        frozen += [9]
    assert list(frozen) == [1, 2, 3]


def test_frozen_list_rejects_remove_pop_clear_sort_reverse() -> None:
    frozen = FrozenList[int]([3, 1, 2])
    with pytest.raises(TypeError):
        frozen.remove(1)
    with pytest.raises(TypeError):
        frozen.pop()
    with pytest.raises(TypeError):
        frozen.clear()
    with pytest.raises(TypeError):
        frozen.sort()
    with pytest.raises(TypeError):
        frozen.reverse()
    assert list(frozen) == [3, 1, 2]


def test_frozen_dict_pickle_roundtrip_reconstructs_via_constructor() -> None:
    """Failure injection: default `dict.__reduce__` round-trips pickling
    through `update`/`__setitem__`, which are blocked above — without the
    `__reduce__` override this would raise `TypeError` instead of
    round-tripping across a process boundary (e.g. `ProcessPoolExecutor`).
    """
    frozen = FrozenDict[str, int]({"a": 1, "b": 2})
    restored = pickle.loads(pickle.dumps(frozen))  # noqa: S301 -- own in-process data
    assert isinstance(restored, FrozenDict)
    assert dict(restored) == {"a": 1, "b": 2}
    with pytest.raises(TypeError):
        restored["a"] = 99


def test_frozen_list_pickle_roundtrip_reconstructs_via_constructor() -> None:
    frozen = FrozenList[int]([1, 2, 3])
    restored = pickle.loads(pickle.dumps(frozen))  # noqa: S301 -- own in-process data
    assert isinstance(restored, FrozenList)
    assert list(restored) == [1, 2, 3]
    with pytest.raises(TypeError):
        restored.append(4)


def test_frozen_dict_generic_subscript_is_usable_at_runtime() -> None:
    """`dict[K, V]` generic alias support (`__class_getitem__`) must still
    resolve at runtime after dropping `type: ignore[type-arg]` — this is
    exactly how `v1.py`'s field validators construct these (e.g.
    `FrozenDict[str, Any](value)`).
    """
    parametrized = FrozenDict[str, int]
    frozen = parametrized({"a": 1})
    assert isinstance(frozen, FrozenDict)
    assert isinstance(frozen, dict)


@pytest.mark.perf
def test_frozen_list_construction_scales_within_budget() -> None:
    """Numeric performance assertion: wrapping a 50,000-entry list (an
    upper-bound `rule_hits`/`reason_codes` size) must stay well under a
    generous 200ms budget — construction is a single `list.__init__` copy,
    no per-element Python-level work.
    """
    payload = list(range(50_000))
    start = time.perf_counter()
    frozen = FrozenList[int](payload)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.2
    assert len(frozen) == 50_000
