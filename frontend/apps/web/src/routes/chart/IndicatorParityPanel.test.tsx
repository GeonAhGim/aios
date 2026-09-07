import "@testing-library/jest-dom/vitest";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import { createDefaultOverlayRegistry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import { VERIFIED_KERNEL_PINS } from "@aios/chart-engine/src/compute/verifiedIndicators";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IndicatorParityPanel, type ServerIndicatorSeriesPort } from "./IndicatorParityPanel";

// CH-18c — component-level tests (no ChartPage/IndicatorPicker mount): the
// real `GET /v1/indicators` network round trip the picker uses is not
// hermetic in this environment (a shared machine may or may not have a
// backend listening on the default base URL — see task-1968), so the
// BBANDS-not-verified scenario is exercised directly against
// IndicatorParityPanel's own props instead of through the live picker UI.

afterEach(() => cleanup());

const registry = createDefaultOverlayRegistry();
const BBANDS = registry.resolve("BBANDS");
const SMA = registry.resolve("SMA");

function candleAt(hourOffset: number): StreamCandle {
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

function manyCandles(count: number): StreamCandle[] {
  return Array.from({ length: count }, (_, i) => candleAt(i));
}

// Catalog carrying only SMA — BBANDS is never in `VERIFIED_KERNEL_PINS` for
// ANY catalog content, so this is enough to prove BBANDS's exclusion is not
// "the catalog hasn't listed it yet" but a permanent client-side refusal.
function smaVerifiedCatalog(): IndicatorCatalogEntry[] {
  const pin = VERIFIED_KERNEL_PINS.SMA!;
  return [
    { name: "SMA", tier: pin.tier, category: "core", version: "ind-v1", hash: pin.entryHash, inputs: ["close"], outputs: ["value"] },
  ];
}

describe("IndicatorParityPanel — CH-18c BBANDS(영구 미검증 지표) 서버 폴백", () => {
  it("negative: BBANDS는 화이트리스트에 오를 수 없어 클라이언트 계산을 쓰지 않고, 서버 값으로 폴백하며 그 사실이 표면화된다", () => {
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(({ name }) => {
      if (name !== "BBANDS") return null;
      return { upperband: [null, 51000], middleband: [null, 50000], lowerband: [null, 49000] };
    });

    render(
      <IndicatorParityPanel
        candles={manyCandles(2)}
        overlays={[BBANDS]}
        catalog={smaVerifiedCatalog()}
        resolveServerSeries={resolveServerSeries}
      />,
    );

    expect(screen.getByTestId("indicator-parity-value-BBANDS")).toHaveTextContent("51000.000000");
    expect(screen.getByTestId("indicator-parity-source-BBANDS")).toHaveTextContent("(server)");
    expect(screen.getByTestId("indicator-parity-fallback-BBANDS")).toBeInTheDocument();
    expect(resolveServerSeries).toHaveBeenCalledWith(expect.objectContaining({ name: "BBANDS" }));
  });

  it("negative: BBANDS를 선택해도 서버 참조가 없으면(포트 미배선) 조용히 사라지지 않고 미검증으로 fail-closed 표시된다", () => {
    render(<IndicatorParityPanel candles={manyCandles(2)} overlays={[BBANDS]} catalog={smaVerifiedCatalog()} />);

    expect(screen.getByTestId("indicator-parity-value-BBANDS")).toHaveTextContent("--");
    expect(screen.getByTestId("indicator-parity-source-BBANDS")).toHaveTextContent("(unverified)");
    expect(screen.queryByTestId("indicator-parity-fallback-BBANDS")).not.toBeInTheDocument();
  });

  it("회귀 방지: SMA처럼 화이트리스트에 있는 지표는 그대로 클라이언트 계산값을 쓴다", () => {
    render(
      <IndicatorParityPanel
        candles={manyCandles(25)}
        overlays={[SMA]}
        catalog={smaVerifiedCatalog()}
        resolveServerSeries={() => ({ value: Array.from({ length: 25 }, (_, i) => (i < 19 ? null : 50200)) })}
      />,
    );

    expect(screen.getByTestId("indicator-parity-value-SMA")).toHaveTextContent("50200.000000");
    expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(client)");
    expect(screen.queryByTestId("indicator-parity-fallback-SMA")).not.toBeInTheDocument();
  });
});
