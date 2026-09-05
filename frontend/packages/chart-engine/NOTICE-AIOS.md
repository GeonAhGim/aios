# NOTICE-AIOS — `@aios/chart-engine` third-party provenance

This package forks **KLineChart** (npm `klinecharts`) under the Apache License 2.0.
This file is the AIOS-side record required by that license: where the code came
from, which files were copied, which were modified (§4(b)), and how the upstream
attribution notices are preserved (§4(c), §4(d)). Selection rationale is in
`docs/design/CHART_ENGINE_FORK_EVAL.md` (CH-0); the bring-in is CH-1b (task-1501).

## 1. Upstream

| Field | Value |
|---|---|
| Project | KLineChart — https://github.com/klinecharts/KLineChart |
| npm package | `klinecharts` |
| Version / tag | `v10.0.3` |
| Tag commit | `e42c1d6b67447bf6c4319aa0c63314460d5e4f8f` |
| Source archive | https://github.com/klinecharts/KLineChart/archive/refs/tags/v10.0.3.tar.gz |
| Archive SHA-256 | `2618045afca0eff5173549c55199f8738919f9a93d9b7ad3293da55b34d12c84` |
| License | Apache License 2.0 (`LICENSE`, copyright holder "lihu") |
| Brought in | 2026-09-05, task-1501 |

## 2. What was copied into `vendor/klinecharts/`

| Path | Origin | Notes |
|---|---|---|
| `vendor/klinecharts/LICENSE` | upstream `LICENSE` | verbatim (201 lines) |
| `vendor/klinecharts/NOTICE` | upstream `NOTICE` | verbatim — see §4 |
| `vendor/klinecharts/licenses/LICENSE-lightweight-charts` | upstream `licenses/` | verbatim — see §4 |
| `vendor/klinecharts/src/**` | upstream `src/` | 154 TypeScript files, verbatim |

Deliberately **not** copied: `dist/`, `docs/`, `debug/`, `scripts/`, `skills/`,
`llms/`, tests, lint/build configs, `package.json`, `pnpm-lock.yaml`. We ship the
source, not the build; the package has **no npm runtime dependency on
`klinecharts`** (CA decision recorded on task-1501) and upstream `src/` has no
third-party imports of its own.

The copy was verified with `diff -r` against the extracted archive at bring-in
time (byte-identical). Git line-ending normalisation (`core.autocrlf`) may
change CRLF/LF on checkout; it changes nothing else.

Because we ship source, the vendor's own `version()` returns the build-time
placeholder `__VERSION__`. The wrapper exposes `KLINECHARTS_VENDOR_VERSION`
(`src/core/klinecharts.ts`) instead.

## 3. Modified vendor files (Apache-2.0 §4(b))

**None.** Every file under `vendor/klinecharts/` is byte-identical to upstream
`v10.0.3`. All AIOS behaviour lives in wrapper code under `src/` (see §5); the
vendor tree is treated as read-only.

If a future leaf must change a vendor file, it has to (a) add a prominent
`// AIOS-MODIFIED: <task id> — <what/why>` header comment to that file and
(b) list the file here with the task id and a one-line description.

## 4. Preserved attribution (Apache-2.0 §4(c), §4(d))

Upstream `v10.0.3` ships a `NOTICE` file, so §4(d) applies: its attribution
notices must travel with any derivative. `vendor/klinecharts/NOTICE` is the
verbatim copy; its content is reproduced here so it is also visible in the
package's own notice file:

```
KLineChart
Copyright (c) 2019 lihu

TradingView Lightweight Charts
Copyright (с) 2019 TradingView, Inc. https://www.tradingview.com
```

Upstream credits TradingView Lightweight Charts (Apache-2.0) because parts of
`src/common/EventHandler.ts` derive from it; upstream carries that project's
license as `licenses/LICENSE-lightweight-charts`, which is copied verbatim.
This is an attribution inside source/notice files only — it does not impose the
on-screen "TradingView" attribution link that Lightweight Charts' own README
asks of direct users, and the CH-0 positioning analysis (D0) is unaffected.

Note for readers of `docs/design/CHART_ENGINE_FORK_EVAL.md` §2.1: that
evaluation recorded "no NOTICE file" for KLineChart. The `v10.0.3` tag does
include one (contents above); this file is the operative record.

The package-level `LICENSE` is the full Apache License 2.0 text as required by
§4(a), copied from upstream. It governs `vendor/klinecharts/`. AIOS-authored
code in `src/` is covered by the repository's own terms.

## 5. AIOS-owned wrapper code (`src/`)

Only `src/core/klinecharts.ts` imports from `vendor/`; everything else goes
through it, so a vendor upgrade or replacement is a one-file change.

| File | Role |
|---|---|
| `src/core/klinecharts.ts` | single import seam over the vendor; re-exports the handful of vendor APIs/types the wrappers use |
| `src/core/klinechartsSeries.ts` | push→pull adapter: candlestick series feed the vendor `DataLoader`; line/histogram series become vendor indicators (`AIOS_SERIES_LINE`, `AIOS_SERIES_HISTOGRAM`) |
| `src/core/klinechartsBackend.ts` | one vendor `Chart` per backend; provides `RendererBackend`, `SeriesBackendFactory`, `TimeScaleBackend`, `PriceScaleBackend`; `createKlinechartsChartEngine()` |
| `src/core/{renderer,timeScale,priceScale,series}.ts` | backend-agnostic contracts (CH-1a) with optional delegation seams |
| `src/core/testing/vendorDomStubs.ts` | jsdom stubs (ResizeObserver, matchMedia, 2D context, clientWidth/Height) so the vendor can lay out under vitest; test support only |

Wrapper files stay ≤300 lines (P6 discipline). `vendor/**` is exempt from that
rule — preserving upstream layout takes precedence.

## 6. Upgrading the vendor

1. Download the new tag archive, record tag, commit SHA and archive SHA-256 in §1.
2. Replace `vendor/klinecharts/{LICENSE,NOTICE,licenses,src}` wholesale; re-run `diff -r`.
3. Re-apply any files listed in §3 (currently none) and update that list.
4. Run `npm run test --workspace=packages/chart-engine` and `npx tsc --noEmit -p packages/chart-engine/tsconfig.json` from `frontend/`.

## 7. Known integration constraints

- `packages/chart-engine/tsconfig.json` mirrors upstream's compiler assumptions
  (`strictPropertyInitialization: false`, `noImplicitAny: false`, no
  `verbatimModuleSyntax`). `apps/web/tsconfig.app.json` enables
  `verbatimModuleSyntax` and `erasableSyntaxOnly`; the vendor sources are not
  clean under those flags (type-only `export default` in `src/common/*.ts`,
  constructor parameter properties). `apps/web` does not import this package yet.
  The leaf that first does (CH-6 `ChartPage.tsx`) must decide between relaxing
  those two flags for web, or building chart-engine as a referenced project
  that emits `.d.ts`.
