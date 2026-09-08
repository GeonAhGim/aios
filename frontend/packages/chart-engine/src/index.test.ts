// @vitest-environment jsdom
// core/klinechartsBackend (re-exported via ./index/core) evaluates vendor
// klinecharts modules that touch `window` at import time — see
// core/klinechartsBackend.test.ts for the same requirement.
import { describe, expect, it } from "vitest";
import * as barrel from "./index";

// task-2100 P6 split: index.ts now only re-exports grouped sub-barrels
// (./index/core, ./index/drawings, ...). This proves every group's exports
// still surface through the top-level "@aios/chart-engine" entry point —
// the one thing that must not change.
describe("chart-engine barrel (index.ts)", () => {
  it("re-exports a representative value from every grouped sub-barrel", () => {
    expect(barrel.createRenderer).toBeTypeOf("function"); // ./index/core
    expect(barrel.DRAWING_KINDS).toBeInstanceOf(Array); // ./index/drawings
    expect(barrel.createCandleStream).toBeTypeOf("function"); // ./index/data
    expect(barrel.createEmptyLayoutModel).toBeTypeOf("function"); // ./index/layout
    expect(barrel.alignSeries).toBeTypeOf("function"); // ./index/compare
    expect(barrel.createPaneModel).toBeTypeOf("function"); // ./index/panes
    expect(barrel.buildStatusLineLegends).toBeTypeOf("function"); // ./index/legend
    expect(barrel.capture).toBeTypeOf("function"); // ./index/templates
    expect(barrel.renderPlot).toBeTypeOf("function"); // ./index/render
    expect(barrel.createDefaultOverlayRegistry).toBeTypeOf("function"); // ./index/indicators
    expect(barrel.computeIndicatorSeries).toBeTypeOf("function"); // ./index/compute
  });

  it("negative: does not export the vendor-typed dataWindowStyle surface (deliberately excluded)", () => {
    expect((barrel as Record<string, unknown>).buildDataWindowStyle).toBeUndefined();
  });
});
