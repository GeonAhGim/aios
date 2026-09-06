/**
 * CH-18b — client/server parity gate: decides, per indicator series, whether
 * the client-computed values (`clientEngine.ts`, CH-18a) are safe to render
 * or whether the server-computed reference must be shown instead.
 *
 * Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.11
 * CH-18 (rest of CH-18 — CH-18a, task-1953, wired the compute path + whitelist
 * gate only; equivalence checking and fallback is this task).
 *
 * Decision (task-1954): `PARITY_TOLERANCE` is pinned to `verify_all.py`'s own
 * `REFERENCE_TOLERANCE` (1e-9, absolute) — never loosened here independently
 * (`__tests__/parityCheck.test.ts` asserts the two constants stay equal via
 * the transplanted `fixtures/referenceVectors.ts`, not a re-typed literal).
 * `null` (lookback not yet met) must match `null` exactly; a null/number
 * disagreement between client and server is itself a mismatch (the two
 * engines disagree about lookback), not something this module treats as
 * "not applicable" and skips.
 *
 * No server reference at all is NOT the same as "matches" — `resolveIndicatorSeries`
 * fails closed to `"unverified"` (renders neither series) rather than trusting
 * unconfirmed client output, mirroring `verifiedIndicators.ts`'s empty-whitelist
 * fail-closed default. A mismatch is never silent: `checkIndicatorParity`'s
 * return value always carries the indicator name, the worst absolute error and
 * an explicit `fallback: true` flag, and `resolveIndicatorSeries` invokes the
 * caller's `onMismatch` hook so a UI layer can log/surface it — nothing here
 * swallows a mismatch into a plain boolean.
 */

import type { IndicatorSeriesResult } from "./clientEngine";

/** Same value as `src/core/indicators/reference/verify_all.py` `REFERENCE_TOLERANCE`. */
export const PARITY_TOLERANCE = 1e-9;

export interface ParityMismatchDetail {
  readonly output: string;
  readonly index: number;
  readonly clientValue: number | null;
  readonly serverValue: number | null;
  readonly absError: number;
}

export type ParityVerdict =
  | { readonly ok: true; readonly name: string }
  | {
      readonly ok: false;
      readonly name: string;
      readonly maxAbsError: number;
      readonly mismatch: ParityMismatchDetail;
      readonly fallback: true;
    };

function absError(client: number | null, server: number | null): number {
  if (client === null && server === null) return 0;
  if (client === null || server === null) return Infinity;
  return Math.abs(client - server);
}

/**
 * Compares one indicator's full client series against the server reference,
 * output-by-output, index-by-index. The server's own output keys drive the
 * comparison — a client series missing one of them is a mismatch (treated as
 * `null` on the client side), never silently skipped.
 */
export function checkIndicatorParity(
  name: string,
  client: IndicatorSeriesResult,
  server: IndicatorSeriesResult,
  tolerance: number = PARITY_TOLERANCE,
): ParityVerdict {
  let worst: ParityMismatchDetail | null = null;
  for (const output of Object.keys(server)) {
    const serverValues = server[output] ?? [];
    const clientValues = client[output];
    for (let index = 0; index < serverValues.length; index += 1) {
      const serverValue = serverValues[index] ?? null;
      const clientValue = clientValues && index < clientValues.length ? (clientValues[index] ?? null) : null;
      const err = absError(clientValue, serverValue);
      if (err > tolerance && (worst === null || err > worst.absError)) {
        worst = { output, index, clientValue, serverValue, absError: err };
      }
    }
  }
  if (worst === null) return { ok: true, name };
  return { ok: false, name, maxAbsError: worst.absError, mismatch: worst, fallback: true };
}

export type IndicatorSeriesSource = "client" | "server" | "unverified";

export interface ResolvedIndicatorSeries {
  readonly source: IndicatorSeriesSource;
  /** `null` only when `source === "unverified"` (no server reference to fall back to either). */
  readonly series: IndicatorSeriesResult | null;
  readonly verdict: ParityVerdict | null;
}

export interface ResolveIndicatorSeriesArgs {
  readonly name: string;
  readonly client: IndicatorSeriesResult;
  /** `null` = no server reference available yet — fails closed to `"unverified"`, never trusts `client` unconfirmed. */
  readonly server: IndicatorSeriesResult | null;
  readonly tolerance?: number;
  /** Observability hook — invoked only on mismatch, so a caller can log/report the fallback (never silent). */
  readonly onMismatch?: (verdict: Extract<ParityVerdict, { ok: false }>) => void;
}

/** The single entry point a UI layer calls: computes the parity verdict and picks which series is safe to render. */
export function resolveIndicatorSeries(args: ResolveIndicatorSeriesArgs): ResolvedIndicatorSeries {
  const { name, client, server, tolerance, onMismatch } = args;
  if (server === null) {
    return { source: "unverified", series: null, verdict: null };
  }
  const verdict = checkIndicatorParity(name, client, server, tolerance);
  if (verdict.ok) {
    return { source: "client", series: client, verdict };
  }
  onMismatch?.(verdict);
  return { source: "server", series: server, verdict };
}
