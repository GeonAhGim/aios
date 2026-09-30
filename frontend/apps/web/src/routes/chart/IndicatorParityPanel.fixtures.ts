import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import { createDefaultOverlayRegistry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import type { Bar } from "@aios/chart-engine/src/compute/clientEngine";
import { VERIFIED_KERNEL_PINS } from "@aios/chart-engine/src/compute/verifiedIndicators";

// Shared fixtures for the IndicatorParityPanel.*.test.tsx split (RATCHET-split,
// task-10200) — kept in a non-test module so none of the split files need to
// re-derive candle/bar/catalog builders independently.

export const registry = createDefaultOverlayRegistry();
export const BBANDS = registry.resolve("BBANDS");
export const SMA = registry.resolve("SMA");

export function candleAt(hourOffset: number): StreamCandle {
  const open = new Date(Date.UTC(2026, 8, 3, hourOffset, 0, 0));
  const close = new Date(Date.UTC(2026, 8, 3, hourOffset + 1, 0, 0));
  return {
    openTimeMs: open.getTime(),
    confirmed: true,
    record: {
      key: { venue: "BITGET", instrument_id: "BTCUSDT", timeframe: "1h" },
      open_time: open.toISOString(),
      close_time: close.toISOString(),
      open: "50000.00",
      high: "50500.00",
      low: "49800.00",
      close: "50200.00",
      volume: "12.5",
      quote_volume: "628500.00",
    },
  };
}

export function manyCandles(count: number): StreamCandle[] {
  return Array.from({ length: count }, (_, i) => candleAt(i));
}

export function manyBars(count: number): Bar[] {
  return Array.from({ length: count }, () => ({ open: 100, high: 101, low: 99, close: 100, volume: 1 }));
}

export function lastDefined(values: ReadonlyArray<number | null>): number | null {
  for (let index = values.length - 1; index >= 0; index -= 1) {
    if (values[index] !== null) return values[index]!;
  }
  return null;
}

/** Minimal fake overlay for gate-only performance tests — the sync gate path (`buildGateRow`) never reads `outputs` when `resolveServerSeries` returns null, so these don't need to resolve to real backend indicators. */
export function fakeOverlay(id: string): OverlayEntry {
  return { id, placement: "main-overlay", params: [], outputs: [{ name: "value", series: "line" }], paneIndex: 0 };
}

// Catalog carrying only SMA — BBANDS is never in `VERIFIED_KERNEL_PINS` for
// ANY catalog content, so this is enough to prove BBANDS's exclusion is not
// "the catalog hasn't listed it yet" but a permanent client-side refusal.
export function smaVerifiedCatalog(): IndicatorCatalogEntry[] {
  const pin = VERIFIED_KERNEL_PINS.SMA!;
  return [
    { name: "SMA", tier: pin.tier, category: "core", version: "ind-v1", hash: pin.entryHash, inputs: ["close"], outputs: ["value"] },
  ];
}
