import { describe, expect, it, vi } from "vitest";
import { createTrendLine } from "../drawings/tools";
import { LayoutModelError, createEmptyLayoutModel, type ChartLayoutModel } from "./layoutModel";
import {
  type ChartingDrawingsRecord,
  type ChartingLayoutRecord,
  type ChartingPort,
  createLayout,
  deleteLayout,
  loadDrawings,
  loadLayout,
  saveDrawings,
  saveLayout,
} from "./persistence";

// Duck-typed like the real ApiError (statusCode/errorCode) — routeApiError.ts
// classifies by shape alone (errorRouting.ts: "어떤 값을 넘겨도 throw하지 않는다"),
// so this layer's tests never need to import api-client's ApiError class.
function apiErrorLike(statusCode: number, errorCode: string): Error & { statusCode: number; errorCode: string } {
  return Object.assign(new Error(errorCode), { statusCode, errorCode });
}

const NOT_FOUND = () => apiErrorLike(404, "RESOURCE_NOT_FOUND");
const CONCURRENCY_CONFLICT = () => apiErrorLike(409, "STATE_CONCURRENCY_CONFLICT");

const sampleModel: ChartLayoutModel = {
  ...createEmptyLayoutModel(),
  panels: [
    {
      id: "p1",
      instrument: { instrumentId: "11111111-1111-1111-1111-111111111111", venue: "BINANCE", symbol: "BTCUSDT" },
      timeframe: "1h",
      indicators: [],
      drawingSetId: "p1-drawings",
      objectTreeOrder: [],
      lockedIndicatorIds: [],
    },
  ],
  activePanelId: "p1",
};

function layoutRecord(overrides: Partial<ChartingLayoutRecord> = {}): ChartingLayoutRecord {
  return {
    id: "layout-1",
    name: "My layout",
    layoutState: {} as Record<string, unknown>,
    revision: 0,
    updatedAt: "2026-09-06T00:00:00Z",
    ...overrides,
  };
}

function fakePort(overrides: Partial<ChartingPort> = {}): ChartingPort {
  return {
    createLayout: vi.fn(),
    listLayouts: vi.fn(),
    getLayout: vi.fn(),
    updateLayout: vi.fn(),
    deleteLayout: vi.fn(),
    getDrawings: vi.fn(),
    putDrawings: vi.fn(),
    ...overrides,
  };
}

describe("round trip: create → load", () => {
  it("saves and restores a multi-panel layout unchanged (wiring proof: layoutState is actually encoded/decoded)", async () => {
    let stored: Record<string, unknown> = {};
    const port = fakePort({
      createLayout: vi.fn(async (input) => {
        stored = input.layoutState;
        return layoutRecord({ layoutState: stored });
      }),
      getLayout: vi.fn(async () => layoutRecord({ layoutState: stored })),
    });

    const created = await createLayout(port, "My layout", sampleModel);
    expect(created.model).toEqual(sampleModel);
    expect(port.createLayout).toHaveBeenCalledWith({ name: "My layout", layoutState: expect.any(Object) });

    const loaded = await loadLayout(port, "layout-1");
    expect(loaded).toEqual({ kind: "ok", value: { meta: created.meta, model: sampleModel } });
  });
});

describe("409 conflict: never auto-overwrites", () => {
  it("saveLayout surfaces STATE_CONCURRENCY_CONFLICT as {kind: 'conflict'}, not an exception", async () => {
    const port = fakePort({ updateLayout: vi.fn().mockRejectedValue(CONCURRENCY_CONFLICT()) });
    const result = await saveLayout(port, "layout-1", 0, { model: sampleModel });
    expect(result).toEqual({ kind: "conflict" });
    expect(port.updateLayout).toHaveBeenCalledWith(
      "layout-1",
      expect.objectContaining({ expectedRevision: 0, name: undefined }),
    );
  });

  it("saveDrawings surfaces STATE_CONCURRENCY_CONFLICT as {kind: 'conflict'}", async () => {
    const port = fakePort({ putDrawings: vi.fn().mockRejectedValue(CONCURRENCY_CONFLICT()) });
    const drawings = [createTrendLine("t", { time: 0, price: 0 }, { time: 1, price: 1 })];
    const result = await saveDrawings(port, "layout-1", 3, drawings);
    expect(result).toEqual({ kind: "conflict" });
    expect(port.putDrawings).toHaveBeenCalledWith(
      "layout-1",
      expect.objectContaining({ expectedRevision: 3, schemaVersion: 1 }),
    );
  });
});

describe("404: distinguished from 'no data yet', including the folded cross-tenant case", () => {
  it("loadLayout returns {kind: 'not_found'} on 404", async () => {
    const port = fakePort({ getLayout: vi.fn().mockRejectedValue(NOT_FOUND()) });
    expect(await loadLayout(port, "ghost")).toEqual({ kind: "not_found" });
  });

  it("cross-tenant access is folded into the same RESOURCE_NOT_FOUND(404) by the server — this layer can't and doesn't try to tell it apart from a genuinely missing id", async () => {
    // application/_shared.py load_owned_layout(): CrossTenantChartLayoutAccessError
    // maps to the identical error_code/status as ChartLayoutNotFoundError.
    const port = fakePort({ getLayout: vi.fn().mockRejectedValue(NOT_FOUND()) });
    const result = await loadLayout(port, "someone-elses-layout");
    expect(result).toEqual({ kind: "not_found" });
  });

  it("saveLayout/deleteLayout/loadDrawings/saveDrawings all surface 404 as {kind: 'not_found'}", async () => {
    const port = fakePort({
      updateLayout: vi.fn().mockRejectedValue(NOT_FOUND()),
      deleteLayout: vi.fn().mockRejectedValue(NOT_FOUND()),
      getDrawings: vi.fn().mockRejectedValue(NOT_FOUND()),
      putDrawings: vi.fn().mockRejectedValue(NOT_FOUND()),
    });
    expect(await saveLayout(port, "ghost", 0, {})).toEqual({ kind: "not_found" });
    expect(await deleteLayout(port, "ghost")).toEqual({ kind: "not_found" });
    expect(await loadDrawings(port, "ghost")).toEqual({ kind: "not_found" });
    expect(await saveDrawings(port, "ghost", 0, [])).toEqual({ kind: "not_found" });
  });

  it("unrelated failures (5xx) are rethrown, never mistaken for 'no data'", async () => {
    const port = fakePort({ getLayout: vi.fn().mockRejectedValue(apiErrorLike(500, "SYSTEM_INTERNAL_ERROR")) });
    await expect(loadLayout(port, "layout-1")).rejects.toThrow("SYSTEM_INTERNAL_ERROR");
  });
});

describe("negative: empty/malformed layout_state is never silently defaulted", () => {
  it("loadLayout throws LayoutModelError instead of returning an empty layout when layoutState is {}", async () => {
    const port = fakePort({ getLayout: vi.fn().mockResolvedValue(layoutRecord({ layoutState: {} })) });
    await expect(loadLayout(port, "layout-1")).rejects.toBeInstanceOf(LayoutModelError);
  });

  it("loadDrawings throws instead of returning an empty collection when the document is malformed", async () => {
    const record: ChartingDrawingsRecord = {
      layoutId: "layout-1",
      document: { drawings: [] }, // missing schema_version
      revision: 0,
      updatedAt: "2026-09-06T00:00:00Z",
    };
    const port = fakePort({ getDrawings: vi.fn().mockResolvedValue(record) });
    await expect(loadDrawings(port, "layout-1")).rejects.toThrow("CHART_DRAWING_SCHEMA_UNSUPPORTED");
  });
});
