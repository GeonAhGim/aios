// CH-18e — moves IndicatorParityPanel's row computation off the render/main
// thread: `computeIndicatorSeries` now runs through a `WorkerPool`
// (`workerPool.ts`, task-1953/2039) instead of synchronously inside render.
//
// The CH-18d whitelist gate (`isVerifiedIndicator`) still runs synchronously,
// in `buildGateRow` below, strictly before any dispatch to the pool — moving
// compute off-thread must never let an unverified indicator's name reach
// `computeIndicatorSeries` even transiently (see IndicatorParityPanel.test.tsx
// CH-18d describe block, which spies on `computeIndicatorSeries` itself, not
// just the rendered outcome).
//
// `pool === null` (no Worker support, or apps/web's `indicatorComputePool.ts`
// wasn't able to spin one up) computes on the main thread instead, wrapped in
// a microtask so the async contract stays uniform — but the DoD forbids a
// *silent* fallback, so a client-sourced row carries a visible `computeNote`
// whenever it was computed this way.
import { useEffect, useState } from "react";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import {
  CLIENT_ENGINE_COMPUTE_TASK,
  computeIndicatorSeries,
  type Bar,
  type ComputeIndicatorSeriesArgs,
  type IndicatorParams,
  type IndicatorSeriesResult,
} from "@aios/chart-engine/src/compute/clientEngine";
import { resolveIndicatorSeries, type IndicatorSeriesSource } from "@aios/chart-engine/src/compute/parityCheck";
import { isVerifiedIndicator } from "@aios/chart-engine/src/compute/verifiedIndicators";
import type { WorkerPool } from "@aios/chart-engine/src/compute/workerPool";

export type ServerIndicatorSeriesPort = (args: {
  readonly name: string;
  readonly params: IndicatorParams;
  readonly bars: readonly Bar[];
}) => IndicatorSeriesResult | null;

const DEFAULT_PARAMS: Readonly<Record<string, IndicatorParams>> = {
  SMA: { timeperiod: 20 },
  EMA: { timeperiod: 20 },
  RSI: { timeperiod: 14 },
  ATR: { timeperiod: 14 },
  CCI: { timeperiod: 14 },
  WILLR: { timeperiod: 14 },
  MFI: { timeperiod: 14 },
  MACD: { fastperiod: 12, slowperiod: 26, signalperiod: 9 },
  STOCH: { fastk_period: 5, slowk_period: 3, slowd_period: 3 },
  OBV: {},
};

function paramsFor(id: string): IndicatorParams {
  return DEFAULT_PARAMS[id] ?? {};
}

function primaryOutput(overlay: OverlayEntry): string {
  return overlay.outputs[0]!.name;
}

function lastNonNull(values: ReadonlyArray<number | null> | undefined): number | null {
  if (!values) return null;
  for (let index = values.length - 1; index >= 0; index -= 1) {
    if (values[index] !== null) return values[index]!;
  }
  return null;
}

export interface IndicatorParityRow {
  readonly id: string;
  readonly source: IndicatorSeriesSource | "pending";
  readonly value: number | null;
  /** Non-null whenever the rendered value is NOT the client-computed one the user would otherwise expect. */
  readonly fallbackReason: string | null;
  /** Non-null only when `source === "client"` but it was computed synchronously (no Worker) — must never be a silent fallback. */
  readonly computeNote: string | null;
}

const NO_NOTE: Pick<IndicatorParityRow, "computeNote"> = { computeNote: null };

/** Synchronous whitelist-gate check — runs before any async dispatch. `"pending"` means "verified, compute is being dispatched". */
function buildGateRow(
  overlay: OverlayEntry,
  bars: readonly Bar[],
  catalog: readonly IndicatorCatalogEntry[],
  resolveServerSeries: ServerIndicatorSeriesPort,
): IndicatorParityRow {
  if (isVerifiedIndicator(overlay.id, catalog)) {
    return { id: overlay.id, source: "pending", value: null, fallbackReason: null, ...NO_NOTE };
  }
  const server = resolveServerSeries({ name: overlay.id, params: paramsFor(overlay.id), bars });
  if (server === null) {
    return { id: overlay.id, source: "unverified", value: null, fallbackReason: null, ...NO_NOTE };
  }
  // eslint-disable-next-line no-console -- DoD: a whitelist-rejected indicator must fall back to the server observably, never silently.
  console.warn(`CH-18d indicator not in verified whitelist, falling back to server: ${overlay.id}`);
  return {
    id: overlay.id,
    source: "server",
    value: lastNonNull(server[primaryOutput(overlay)]),
    fallbackReason: "클라이언트 미검증: 화이트리스트 불일치",
    ...NO_NOTE,
  };
}

/** Dispatches the already-gated compute: through the pool when one exists, else a same-thread microtask. */
function dispatchCompute(
  pool: WorkerPool | null,
  args: ComputeIndicatorSeriesArgs,
): Promise<IndicatorSeriesResult> {
  if (pool) return pool.submit<ComputeIndicatorSeriesArgs, IndicatorSeriesResult>(CLIENT_ENGINE_COMPUTE_TASK, args);
  return Promise.resolve().then(() => computeIndicatorSeries(args));
}

export function useIndicatorParityRows(
  bars: readonly Bar[],
  overlays: readonly OverlayEntry[],
  catalog: readonly IndicatorCatalogEntry[],
  resolveServerSeries: ServerIndicatorSeriesPort,
  pool: WorkerPool | null,
): readonly IndicatorParityRow[] {
  const gateRows = overlays.map((overlay) => buildGateRow(overlay, bars, catalog, resolveServerSeries));
  const [resolved, setResolved] = useState<ReadonlyMap<string, IndicatorParityRow>>(new Map());

  useEffect(() => {
    let cancelled = false;
    setResolved(new Map());

    for (const overlay of overlays) {
      if (!isVerifiedIndicator(overlay.id, catalog)) continue;
      const params = paramsFor(overlay.id);
      const output = primaryOutput(overlay);

      dispatchCompute(pool, { name: overlay.id, params, bars, catalog })
        .then((client) => {
          if (cancelled) return;
          const server = resolveServerSeries({ name: overlay.id, params, bars });
          const result = resolveIndicatorSeries({
            name: overlay.id,
            client,
            server,
            onMismatch: (verdict) => {
              // eslint-disable-next-line no-console -- DoD: fallback must surface observably, never silently.
              console.warn(`CH-18b indicator parity fallback: ${verdict.name} maxAbsError=${verdict.maxAbsError}`);
            },
          });
          setResolved((prev) =>
            new Map(prev).set(overlay.id, {
              id: overlay.id,
              source: result.source,
              value: lastNonNull(result.series?.[output]),
              fallbackReason: result.verdict && !result.verdict.ok ? `최대오차 ${result.verdict.maxAbsError.toExponential(3)}` : null,
              computeNote: result.source === "client" && pool === null ? "워커 미지원: 메인 스레드에서 동기 계산" : null,
            }),
          );
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          const server = resolveServerSeries({ name: overlay.id, params, bars });
          if (server === null) {
            setResolved((prev) => new Map(prev).set(overlay.id, { id: overlay.id, source: "unverified", value: null, fallbackReason: null, ...NO_NOTE }));
            return;
          }
          const detail = err instanceof Error ? err.message : String(err);
          // eslint-disable-next-line no-console -- DoD: a compute failure must still fall back to the server observably, never silently.
          console.warn(`CH-18e indicator compute failed, falling back to server: ${overlay.id} (${detail})`);
          setResolved((prev) =>
            new Map(prev).set(overlay.id, {
              id: overlay.id,
              source: "server",
              value: lastNonNull(server[output]),
              fallbackReason: `클라이언트 계산 실패: ${detail}`,
              ...NO_NOTE,
            }),
          );
        });
    }

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- gateRows is derived from these same deps each render, not an independent input.
  }, [bars, overlays, catalog, resolveServerSeries, pool]);

  return gateRows.map((row) => resolved.get(row.id) ?? row);
}
