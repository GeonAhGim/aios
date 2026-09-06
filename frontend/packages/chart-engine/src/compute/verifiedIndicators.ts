/**
 * CH-18a — client-side indicator whitelist gate.
 *
 * Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.11
 * CH-18. Reference-vector source: IND-7g (task-1738)
 * `src/core/indicators/reference/verify_all.py` — of the 11 indicators with
 * BOTH `engine.incremental` and `engine.vectorized` kernels
 * (`VERIFIABLE_NAMES`), 10 actually pass the 3-way cross-check
 * (TA-Lib C / incremental / vectorized) within `REFERENCE_TOLERANCE`.
 * BBANDS is a `VERIFIABLE_NAMES` candidate but is NOT in `verified` —
 * `run_verification()` excludes it at the `timeperiod=2` boundary (TA-Lib's
 * `E[X^2]-E[X]^2` variance formula loses more precision there than this
 * codebase's `E[(X-mean)^2]`, `tests/unit/core/indicators/test_reference_verify_all.py::test_full_verification_matches_known_state`
 * pins `report.excluded == ("BBANDS",)`) — so it is out of scope here too,
 * not just unpinned. `clientEngine.ts` ports exactly the 10 verified
 * kernels to TS; this module decides, at runtime, whether a given server
 * catalog entry (IND-12 `GET /v1/indicators`) is still safe to compute
 * client-side.
 *
 * Decision (task-1953): the eligibility check is NOT "is this name in a
 * hardcoded list" — it is "does the LIVE catalog entry's (tier, entry_hash)
 * still match the pin recorded when the TS kernel was verified". The pins
 * below are themselves derived from the server registry (`entry_hash` =
 * `registry_tiers.CatalogEntry.entry_hash`, sha256 of `canonical_spec_dict`)
 * at the time the corresponding kernel was ported — they anchor the check,
 * they do not replace it. If the server's canonical spec for a name ever
 * changes (default param, input/output shape, TA-Lib version), its
 * `entry_hash` changes and this module excludes it again until the pin is
 * refreshed against a re-verified kernel. No catalog, or a catalog without a
 * matching entry, fails closed to an EMPTY whitelist — never to "assume the
 * pinned name is fine offline" (decision, klinecharts vendor indicators must
 * never be substituted here either — see clientEngine.ts).
 */

import type { IndicatorCatalogEntry, IndicatorTier } from "../plugins/indicatorPlugin";

export interface VerifiedKernelPin {
  readonly tier: IndicatorTier;
  readonly entryHash: string;
}

/**
 * Pinned against `DEFAULT_STATIC_CATALOG` entries for the 10 names in
 * `verify_all.run_verification().verified`
 * (SMA/EMA/RSI/ATR/CCI/WILLR/MFI/MACD/STOCH/OBV — IND-7g task-1738 3-way
 * verification; BBANDS is deliberately absent, see module docstring) as of
 * REGISTRY_VERSION "ind-v1". Recompute with
 * `registry_tiers.DEFAULT_STATIC_CATALOG[name].entry_hash` whenever a
 * kernel below is re-ported after a server spec change.
 */
export const VERIFIED_KERNEL_PINS: Readonly<Record<string, VerifiedKernelPin>> = Object.freeze({
  SMA: { tier: "core", entryHash: "a84490f7e87708aa2f5e130507955c92293b5c737c67397e686df69a6ceefd30" },
  EMA: { tier: "core", entryHash: "1c712aa86505ca9680c75b82bf92c68606fdc957c2fbc3e58bc7fcdf8a264037" },
  RSI: { tier: "core", entryHash: "1e5b7c84da41c1bd9e8c6657bdd18d5ab2695748ab0353cabbdd19742a9b1431" },
  ATR: { tier: "core", entryHash: "4ccff1c94825806fa709d841594f3f9ec5641f4d902e09b7095e80315191c62e" },
  CCI: { tier: "core", entryHash: "3ae08305e245bc86b7b8f7a1b6cc0091822faf8b07e0698bde9cd7161f9708b4" },
  WILLR: { tier: "core", entryHash: "831d3721fe6a2b4ce79e4ab4399177fd4687095f9b97df6c0ec203c5302d42b3" },
  MFI: { tier: "core", entryHash: "c1b9b70cc4b281af4d54d0019f980f5b2d64b1a7ae1bd73cc7ec9d19b3025642" },
  MACD: { tier: "core", entryHash: "8f6e17ff32808cebb7e7702afa30a3b9347502cf491a8c1143f58f1dc2418fb6" },
  STOCH: { tier: "core", entryHash: "2bcc899085d81ae29a257375e821da8eab91927889377e17f22764958307ee09" },
  OBV: { tier: "core", entryHash: "5fa69d3d157467164ce79483ac889c347cf44b3f9d4484f584f97c05274854de" },
});

/**
 * Resolves the subset of `catalog` that is currently safe for client-side
 * compute: pinned name, matching tier, matching entry_hash. `catalog` being
 * absent/empty (offline, load failure) returns an empty map — fail-closed,
 * never a cached/offline guess (decision).
 */
export function resolveVerifiedIndicators(
  catalog: readonly IndicatorCatalogEntry[] | null | undefined,
): ReadonlyMap<string, IndicatorCatalogEntry> {
  const whitelist = new Map<string, IndicatorCatalogEntry>();
  if (!catalog) return whitelist;
  for (const entry of catalog) {
    const pin = VERIFIED_KERNEL_PINS[entry.name];
    if (!pin) continue;
    if (entry.tier !== pin.tier) continue;
    if (entry.hash !== pin.entryHash) continue;
    whitelist.set(entry.name, entry);
  }
  return whitelist;
}

export function isVerifiedIndicator(
  name: string,
  catalog: readonly IndicatorCatalogEntry[] | null | undefined,
): boolean {
  return resolveVerifiedIndicators(catalog).has(name);
}
