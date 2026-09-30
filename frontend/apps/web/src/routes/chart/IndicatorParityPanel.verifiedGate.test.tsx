import "../../i18n";
import "@testing-library/jest-dom/vitest";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import { computeIndicatorSeries, ClientEngineError } from "@aios/chart-engine/src/compute/clientEngine";
import { VERIFIED_KERNEL_PINS } from "@aios/chart-engine/src/compute/verifiedIndicators";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IndicatorParityPanel, type ServerIndicatorSeriesPort } from "./IndicatorParityPanel";
import { SMA, BBANDS, manyCandles, manyBars, smaVerifiedCatalog, fakeOverlay } from "./IndicatorParityPanel.fixtures";

// CH-18d — spy on the real `computeIndicatorSeries`, keeping its actual
// implementation, so the tests below can assert it was never called for a
// whitelist-rejected indicator instead of only asserting the rendered
// outcome (the wiring gap this task closes is exactly "the gate exists but
// nothing calls it before compute" — a screen-only assertion would not have
// caught that).
vi.mock("@aios/chart-engine/src/compute/clientEngine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@aios/chart-engine/src/compute/clientEngine")>();
  return { ...actual, computeIndicatorSeries: vi.fn(actual.computeIndicatorSeries) };
});

afterEach(() => {
  cleanup();
  vi.mocked(computeIndicatorSeries).mockClear();
});

// CH-18d — verifiedIndicators.ts's whitelist gate must sit in front of
// computeIndicatorSeries in this exact component, not merely exist somewhere
// in chart-engine. Both tests below assert the compute spy's call count, not
// just the rendered outcome — reverting the `isVerifiedIndicator` pre-check
// in IndicatorParityPanel.tsx's `buildRow` makes both fail because
// computeIndicatorSeries would then run once (and throw internally) instead
// of never running at all.
describe("IndicatorParityPanel — CH-18d verifiedIndicators 실배선", () => {
  it("negative ①: verify_all.py가 검증하지 못한 지표(BBANDS)는 computeIndicatorSeries를 한 번도 호출하지 않는다", () => {
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

    expect(computeIndicatorSeries).not.toHaveBeenCalled();
    expect(screen.getByTestId("indicator-parity-source-BBANDS")).toHaveTextContent("(server)");
    expect(screen.getByTestId("indicator-parity-fallback-BBANDS")).toBeInTheDocument();
  });

  it("negative ②: VERIFIED_KERNEL_PINS와 entry_hash가 다른(핀 드리프트) 카탈로그 항목은 computeIndicatorSeries를 호출하지 않고 서버로 폴백한다", () => {
    const pin = VERIFIED_KERNEL_PINS.SMA!;
    const driftedCatalog: IndicatorCatalogEntry[] = [
      { name: "SMA", tier: pin.tier, category: "core", version: "ind-v1", hash: "0".repeat(64), inputs: ["close"], outputs: ["value"] },
    ];
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(() => ({ value: [50200] }));

    render(
      <IndicatorParityPanel
        candles={manyCandles(25)}
        overlays={[SMA]}
        catalog={driftedCatalog}
        resolveServerSeries={resolveServerSeries}
      />,
    );

    expect(computeIndicatorSeries).not.toHaveBeenCalled();
    expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(server)");
    expect(screen.getByTestId("indicator-parity-fallback-SMA")).toBeInTheDocument();
    expect(resolveServerSeries).toHaveBeenCalledWith(expect.objectContaining({ name: "SMA" }));
  });

  it("회귀 방지: 화이트리스트를 통과하는 지표는 여전히 computeIndicatorSeries를 호출해 클라이언트 계산을 쓴다", async () => {
    render(
      <IndicatorParityPanel
        candles={manyCandles(25)}
        overlays={[SMA]}
        catalog={smaVerifiedCatalog()}
        resolveServerSeries={() => ({ value: Array.from({ length: 25 }, (_, i) => (i < 19 ? null : 50200)) })}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("indicator-parity-source-SMA")).toHaveTextContent("(client)"));
    expect(computeIndicatorSeries).toHaveBeenCalledTimes(1);
  });

  // Failure injection + automated gate-red repro: before b82607b7, buildRow
  // called computeIndicatorSeries first and caught the `ClientEngineError` it
  // throws internally (createClientIncrementalIndicator's second line of
  // defense — a real implementation, not a reimplementation or stub) to fall
  // back. Reproducing that reverted order here by calling the real
  // `computeIndicatorSeries` first, directly, against an unverified indicator
  // really does throw (red: 1 call + a real error). The very next lines
  // render the actual component with the same fixture and it stays at 0
  // calls (green) — no git revert, no fake reimplementation of
  // computeIndicatorSeries, contrasted automatically within this file.
  it("negative ③(failure injection + automated gate-red repro): calling computeIndicatorSeries first without the pre-gate (repro) throws a real ClientEngineError (red) vs the actual component blocks it at 0 calls (green)", () => {
    const bars = manyBars(2);
    const catalog = smaVerifiedCatalog();

    // Red: reproduces the pre-b82607b7 order — calls the real
    // computeIndicatorSeries directly against BBANDS (unverified) without any
    // pre-gate. This is the same real implementation the component uses, not
    // a stub, so it shows exactly what happens without the gate.
    let thrown: unknown;
    try {
      computeIndicatorSeries({ name: "BBANDS", params: {}, bars, catalog });
    } catch (err) {
      thrown = err;
    }
    expect(thrown).toBeInstanceOf(ClientEngineError);
    expect((thrown as InstanceType<typeof ClientEngineError>).code).toBe("CLIENT_ENGINE_INDICATOR_NOT_VERIFIED");
    expect(computeIndicatorSeries).toHaveBeenCalledTimes(1);
    vi.mocked(computeIndicatorSeries).mockClear();

    // Green: the actual component (pre-gate in place) never calls it, even with the same fixture.
    const resolveServerSeries: ServerIndicatorSeriesPort = vi.fn(({ name }) => {
      if (name !== "BBANDS") return null;
      return { upperband: [null, 51000], middleband: [null, 50000], lowerband: [null, 49000] };
    });
    render(<IndicatorParityPanel candles={manyCandles(2)} overlays={[BBANDS]} catalog={catalog} resolveServerSeries={resolveServerSeries} />);
    expect(computeIndicatorSeries).not.toHaveBeenCalled();
    expect(screen.getByTestId("indicator-parity-source-BBANDS")).toHaveTextContent("(server)");
  });

  // Numeric performance: buildGateRow's synchronous whitelist check
  // (isVerifiedIndicator -> resolveVerifiedIndicators) reruns on every render,
  // scaling with overlay count x catalog size (no caching, per the module's
  // own decision doc). It must not noticeably block rendering even at a
  // real-service catalog scale — same axis as the CH-18c numeric test.
  it("numeric performance: the CH-18d gate path renders within an 8000ms budget for a 5,000-entry catalog x 40 overlays", () => {
    const staleEntries: IndicatorCatalogEntry[] = Array.from({ length: 5000 }, (_, i) => ({
      name: `LEGACY_${i}`,
      tier: "core",
      category: "core",
      version: "ind-v1",
      hash: "0".repeat(64),
      inputs: ["close"],
      outputs: ["value"],
    }));
    const catalog = [...staleEntries, ...smaVerifiedCatalog()];
    const overlays = [SMA, ...Array.from({ length: 39 }, (_, i) => fakeOverlay(`LEGACY_${i}`))];

    const start = performance.now();
    render(<IndicatorParityPanel candles={manyCandles(2)} overlays={overlays} catalog={catalog} resolveServerSeries={() => null} />);
    const elapsedMs = performance.now() - start;

    // The 39 fake overlay names aren't in the catalog, so they fail closed to
    // unverified immediately; only SMA passes the whitelist and stays
    // "pending" (the effect hasn't run yet) — this only measures the sync
    // gate's own cost, so client compute itself isn't asserted here.
    expect(screen.getByTestId("indicator-parity-source-LEGACY_0")).toHaveTextContent("(unverified)");
    // 8s: wider margin than the CH-18c numeric test's 3s because this shared host
    // was observed running this suite alongside other worker fleets' concurrent
    // test runs (isolated single-file run measures well under 500ms; a fleet-
    // contended run measured ~5.4s) — still tight enough to catch an O(n^2)+
    // regression in the per-overlay whitelist scan.
    expect(elapsedMs).toBeLessThan(8000);
  });
});
