/**
 * CH-11 — server indicator catalog (IND-12, `GET /v1/indicators` 3-tier
 * CORE/OSS/SCRIPT catalog) query/fetch contract, moved out of
 * `indicatorPlugin.ts` (P6 300-line split, pure move, re-exported from
 * there so `plugins/indicatorPlugin.ts`'s public import path is unchanged).
 * The plugin registry itself (registerIndicatorPlugin etc.) stays in
 * `indicatorPlugin.ts` and consumes `IndicatorCatalogEntry` from here.
 */

import { routeApiError, type RoutedApiError } from "@aios/shared-types";

export type IndicatorTier = "core" | "oss" | "script";

/** Mirrors `IndicatorListItemView` (`src/api/schemas/indicators.py`, IND-12). */
export interface IndicatorCatalogEntry {
  readonly name: string;
  readonly tier: IndicatorTier;
  readonly category: string;
  readonly version: string;
  readonly hash: string;
  readonly inputs: readonly string[];
  readonly outputs: readonly string[];
}

export interface IndicatorCatalogQuery {
  readonly q?: string;
  readonly category?: string;
  readonly cursor?: string;
  readonly limit?: number;
}

export interface IndicatorCatalogPage {
  readonly items: readonly IndicatorCatalogEntry[];
  readonly nextCursor: string | null;
}

/** Injected port — matches `@aios/api-client` `clients/indicators.ts` 1:1. */
export interface IndicatorCatalogPort {
  listIndicators(query?: IndicatorCatalogQuery): Promise<IndicatorCatalogPage>;
}

export type IndicatorCatalogLoadResult =
  | { readonly kind: "ok"; readonly page: IndicatorCatalogPage }
  | { readonly kind: "error"; readonly routed: RoutedApiError };

// A list endpoint has no optimistic-locking states (unlike persistence.ts's
// conflict/not_found) — every failure, network or parsing, collapses to one
// classification via routeApiError. routeApiError never throws (falls back
// to "unknown"), so this never re-hides a failure the caller didn't ask for.
export async function loadIndicatorCatalog(
  port: IndicatorCatalogPort,
  query: IndicatorCatalogQuery = {},
): Promise<IndicatorCatalogLoadResult> {
  try {
    return { kind: "ok", page: await port.listIndicators(query) };
  } catch (err) {
    return { kind: "error", routed: routeApiError(err) };
  }
}
