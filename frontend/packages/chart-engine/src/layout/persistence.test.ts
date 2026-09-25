import { describe, expect, it, vi } from "vitest";
import { createTrendLine } from "../drawings/tools";
import { LayoutModelError, createEmptyLayoutModel, encodeLayoutModel, type ChartLayoutModel } from "./layoutModel";
import {
  type ChartingDrawingsRecord,
  type ChartingLayoutRecord,
  type ChartingPort,
  createLayout,
  deleteLayout,
  listLayouts,
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

describe("numeric perf: listLayouts decode budget", () => {
  it("decodes 500 layout records (multi-panel model each) within a 200ms budget", async () => {
    const encoded = encodeLayoutModel(sampleModel);
    const records: ChartingLayoutRecord[] = Array.from({ length: 500 }, (_, i) =>
      layoutRecord({ id: `layout-${i}`, layoutState: encoded }),
    );
    const port = fakePort({ listLayouts: vi.fn(async () => records) });

    const startedAt = performance.now();
    const result = await listLayouts(port);
    const elapsedMs = performance.now() - startedAt;

    expect(result).toHaveLength(500);
    expect(elapsedMs).toBeLessThan(200);
  });
});

// classify()'s "conflict" branch on GET-only/no-revision endpoints (getLayout,
// deleteLayout, getDrawings) is defensive: the server contract (charting.py)
// never returns 409 for them, so loadLayout/deleteLayout/loadDrawings each
// throw a diagnostic "unexpected conflict classification on <op>" Error
// instead of falling through to `outcome.value`, which doesn't exist on the
// conflict variant and would otherwise surface as an opaque
// "Cannot read properties of undefined" crash. These pin that diagnostic
// message: delete the guard and these fail with a different, worse error,
// which is exactly the gate-red this leaf reproduces.
describe("gate-red reproduction: contract-violation guard on conflict-impossible ops", () => {
  it("loadLayout throws a diagnostic error (not an undefined-property crash) if the server ever 409s on GET", async () => {
    const port = fakePort({ getLayout: vi.fn().mockRejectedValue(CONCURRENCY_CONFLICT()) });
    await expect(loadLayout(port, "layout-1")).rejects.toThrow("unexpected conflict classification on getLayout");
  });

  it("deleteLayout throws a diagnostic error if the server ever 409s on DELETE (delete_layout takes no expected_revision)", async () => {
    const port = fakePort({ deleteLayout: vi.fn().mockRejectedValue(CONCURRENCY_CONFLICT()) });
    await expect(deleteLayout(port, "layout-1")).rejects.toThrow("unexpected conflict classification on delete_layout");
  });

  it("loadDrawings throws a diagnostic error if the server ever 409s on GET drawings", async () => {
    const port = fakePort({ getDrawings: vi.fn().mockRejectedValue(CONCURRENCY_CONFLICT()) });
    await expect(loadDrawings(port, "layout-1")).rejects.toThrow("unexpected conflict classification on getDrawings");
  });
});

describe("D3: adversarial + multi-instance", () => {
  it("adversarial: a server-supplied layoutState carrying a __proto__ pollution attempt is rejected, not merged into the model or Object.prototype", async () => {
    const malicious = { ...(encodeLayoutModel(sampleModel) as Record<string, unknown>) };
    // JSON.parse (untrusted server body) produces an own enumerable
    // "__proto__" data property, not a prototype mutation — defineProperty
    // reproduces that exactly (a literal `{ __proto__: ... }` would instead
    // set the actual prototype and not exercise assertKnownFields at all).
    Object.defineProperty(malicious, "__proto__", {
      value: { polluted: true },
      enumerable: true,
      configurable: true,
      writable: true,
    });
    const port = fakePort({ getLayout: vi.fn(async () => layoutRecord({ layoutState: malicious })) });

    await expect(loadLayout(port, "layout-1")).rejects.toBeInstanceOf(LayoutModelError);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  it("multi-instance: two concurrent loadLayout calls against independent ports/ids never cross-contaminate results (no shared module state)", async () => {
    const modelA: ChartLayoutModel = { ...sampleModel, activePanelId: "p1" };
    const modelB: ChartLayoutModel = { ...sampleModel, activePanelId: null };
    const portA = fakePort({
      getLayout: vi.fn(async () => layoutRecord({ id: "layout-a", layoutState: encodeLayoutModel(modelA) })),
    });
    const portB = fakePort({
      getLayout: vi.fn(async () => layoutRecord({ id: "layout-b", layoutState: encodeLayoutModel(modelB) })),
    });

    const [resultA, resultB] = await Promise.all([loadLayout(portA, "layout-a"), loadLayout(portB, "layout-b")]);

    expect(resultA).toEqual({ kind: "ok", value: { meta: expect.objectContaining({ id: "layout-a" }), model: modelA } });
    expect(resultB).toEqual({ kind: "ok", value: { meta: expect.objectContaining({ id: "layout-b" }), model: modelB } });
  });
});
