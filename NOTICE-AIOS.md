# NOTICE-AIOS — root-level third-party provenance

This file is the AIOS-side record of third-party code/packages that carry a
notice obligation, required by their licenses. Package-scoped provenance for
frontend workspaces lives next to those packages (e.g.
`frontend/packages/chart-engine/NOTICE-AIOS.md`); this file covers the
Python backend (`pyproject.toml` dependencies).

## 1. pandas-ta-classic (IND-11)

| Field | Value |
|---|---|
| Package | `pandas-ta-classic` (PyPI), repository `xgboosted/pandas-ta-classic` |
| Version constraint | `>=0.6.52` (`pyproject.toml` `[project.dependencies]`) |
| License | MIT — wheel `LICENSE`: "The MIT License (MIT) / Copyright (c) 2021+ pandas-ta contributors / Copyright (c) 2024+ pandas-ta-classic contributors (xgboosted/pandas-ta-classic)" |
| Selection rationale | `docs/design/INDICATOR_OSS_EVAL.md` §2.3, §6 (IND-9 evaluation, score 88/100) |
| Decision record | `docs/design/ADR-2026-09-09-A-ind11-pandas-ta-classic.md` — spec §9.9 IND-11 target package changed from the original `pandas-ta` (Python>=3.12 requirement, repository returns 404, `numba` pin) to this fork |
| Brought in | 2026-09-23, task-4035 (dependency declaration only; the bridge adapter `adapters/pandas_ta_bridge.py` and indicator registration are separate leaves) |

### MIT attribution

`pandas-ta-classic` preserves the original `pandas-ta` copyright line in its
own `LICENSE` file (see table above), satisfying the MIT "the above copyright
notice ... shall be included in all copies or substantial portions of the
Software" condition for the code it forked. AIOS consumes this package as an
ordinary PyPI dependency (`import pandas_ta_classic`), not as vendored/copied
source, so no separate copy of upstream source files lives in this
repository; the obligation is satisfied by depending on the unmodified
published distribution and recording its license and origin here.

### `oracle` extra is forbidden — LGPL-3.0 (tulipy)

`pandas-ta-classic` ships an optional `oracle` extra
(`pandas-ta-classic[oracle]`) that pulls in `tulipy` (Python binding for
`tulipindicators`, **LGPL-3.0** — see `docs/design/INDICATOR_OSS_EVAL.md` §5)
as a test oracle. AIOS treats GPL/LGPL code as excluded from the repository
in any form (ADR-2026-09-06-F, INDICATOR_OSS_EVAL.md §5 "실무 규칙"):

- `pyproject.toml` MUST declare the bare `pandas-ta-classic` distribution
  only — never `pandas-ta-classic[oracle]` or any extra that resolves to
  `tulipy`.
- No environment this repository's tests or CI run in (`.venv`, IND-13
  nightly job, etc.) may have `tulipy` installed. `pip show tulipy` must
  report "not found" (see
  `tests/unit/core/indicators/test_pandas_ta_classic_dependency_ind11.py`).
- `src/core/indicators/oss_eval_gate.py` (`BANNED_LICENSE_PACKAGES`) already
  fails the build if `tulipy`, `tulipindicators`, `backtrader`, or
  `nautilus-trader` is ever declared as a real dependency; this file records
  the same rule for the specific `oracle` extra vector.

### TA-Lib overlap

Per ADR-2026-09-06-F, indicators already covered by the TA-Lib bridge
(IND-10, 161 functions) are excluded from `pandas-ta-classic` registration —
only the net-incremental set (indicators TA-Lib does not provide) is
registered by the bridge adapter. That registration work is out of scope for
this file; see the IND-11 bridge leaf for the net-incremental count and the
zero-duplicate check.
