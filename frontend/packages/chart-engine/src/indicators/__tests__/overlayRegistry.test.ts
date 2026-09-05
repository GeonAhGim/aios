import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  DEFAULT_OVERLAY_DEFINITIONS,
  INDICATOR_REGISTRY_VERSION,
  MAIN_PANE_INDEX,
  OverlayRegistryError,
  createDefaultOverlayRegistry,
  createOverlayRegistry,
  type OverlayDefinition,
} from "../overlayRegistry";

const sma: OverlayDefinition = {
  id: "SMA",
  placement: "main-overlay",
  params: ["timeperiod"],
  outputs: [{ name: "value", series: "line" }],
};
const rsi: OverlayDefinition = {
  id: "RSI",
  placement: "sub-pane",
  params: ["timeperiod"],
  outputs: [{ name: "value", series: "line" }],
};
const macd: OverlayDefinition = {
  id: "MACD",
  placement: "sub-pane",
  params: ["fastperiod", "slowperiod", "signalperiod"],
  outputs: [
    { name: "macd", series: "line" },
    { name: "signal", series: "line" },
    { name: "hist", series: "histogram" },
  ],
};

function expectError(fn: () => unknown, code: OverlayRegistryError["code"], id: string): void {
  let caught: unknown;
  try {
    fn();
  } catch (e) {
    caught = e;
  }
  expect(caught).toBeInstanceOf(OverlayRegistryError);
  const err = caught as OverlayRegistryError;
  expect(err.code).toBe(code);
  expect(err.indicatorId).toBe(id);
  expect(err.message).toContain(code);
}

describe("OverlayRegistry pane assignment", () => {
  it("puts main overlays on pane 0 and gives each sub-pane indicator its own pane", () => {
    const registry = createOverlayRegistry();
    expect(registry.paneCount).toBe(1);

    expect(registry.register(sma).paneIndex).toBe(MAIN_PANE_INDEX);
    expect(registry.register(rsi).paneIndex).toBe(1);
    expect(registry.register(macd).paneIndex).toBe(2);
    expect(registry.register({ ...sma, id: "EMA" }).paneIndex).toBe(MAIN_PANE_INDEX);
    expect(registry.paneCount).toBe(3);
  });

  it("resolve returns the registered entry with a defensive copy of the definition", () => {
    const registry = createOverlayRegistry([macd]);
    const entry = registry.resolve("MACD");
    expect(entry).toMatchObject({ ...macd, paneIndex: 1 });
    expect(entry.outputs).not.toBe(macd.outputs);
    expect(registry.has("MACD")).toBe(true);
    expect(registry.list().map((e) => e.id)).toEqual(["MACD"]);
  });

  it("list returns a snapshot that does not alias internal state", () => {
    const registry = createOverlayRegistry([sma]);
    const snapshot = registry.list() as unknown as unknown[];
    snapshot.pop();
    expect(registry.list()).toHaveLength(1);
  });
});

describe("OverlayRegistry negative paths", () => {
  it("rejects duplicate ids without consuming a pane index", () => {
    const registry = createOverlayRegistry([rsi]);
    expectError(() => registry.register({ ...rsi, placement: "main-overlay" }), "CHART_OVERLAY_DUPLICATE", "RSI");
    expect(registry.resolve("RSI").placement).toBe("sub-pane");
    expect(registry.register(macd).paneIndex).toBe(2);
  });

  it("throws on unknown ids instead of silently falling back", () => {
    const registry = createOverlayRegistry([sma]);
    expectError(() => registry.resolve("sma"), "CHART_OVERLAY_UNKNOWN", "sma");
    expectError(() => registry.resolve("VWAP"), "CHART_OVERLAY_UNKNOWN", "VWAP");
    expect(registry.has("VWAP")).toBe(false);
  });

  it.each<[string, OverlayDefinition, string]>([
    ["empty id", { ...sma, id: "" }, ""],
    ["unknown placement", { ...sma, placement: "footer" as OverlayDefinition["placement"] }, "SMA"],
    ["no outputs", { ...sma, outputs: [] }, "SMA"],
    ["duplicate output", { ...macd, outputs: [macd.outputs[0], macd.outputs[0]] }, "MACD"],
    ["duplicate param", { ...macd, params: ["fastperiod", "fastperiod"] }, "MACD"],
    ["empty param name", { ...sma, params: [""] }, "SMA"],
  ])("rejects an invalid definition (%s) and leaves the registry untouched", (_label, definition, id) => {
    const registry = createOverlayRegistry();
    expectError(() => registry.register(definition), "CHART_OVERLAY_INVALID", id);
    expect(registry.list()).toEqual([]);
    expect(registry.paneCount).toBe(1);
  });

  it("fails fast on the first invalid seed definition", () => {
    expectError(() => createOverlayRegistry([sma, { ...sma, id: "" }]), "CHART_OVERLAY_INVALID", "");
    expectError(() => createOverlayRegistry([sma, sma]), "CHART_OVERLAY_DUPLICATE", "SMA");
  });
});

// --- Drift guard: backend IndicatorRegistry (L01/L02) is the SSOT -----------

const here = dirname(fileURLToPath(import.meta.url));
const backendIndicators = resolve(here, "../../../../../../src/core/indicators");

interface PySpec {
  readonly params: string[];
  readonly outputs: string[];
}

function parseTalibSpecs(source: string): Map<string, PySpec> {
  const specs = new Map<string, PySpec>();
  const entry = /"([A-Z0-9_]+)":\s*IndicatorSpec\(([\s\S]*?)\n {4}\),/g;
  for (const match of source.matchAll(entry)) {
    const [, name, body] = match;
    const params = [...body.matchAll(/_period\("([a-z_]+)"/g)].map((m) => m[1]);
    const outputsRaw = /outputs=\(([^)]*)\)/.exec(body)?.[1] ?? "";
    const outputs = [...outputsRaw.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
    specs.set(name, { params, outputs });
  }
  return specs;
}

describe("drift guard against backend src/core/indicators (SSOT)", () => {
  const talib = readFileSync(resolve(backendIndicators, "specs_talib.py"), "utf8");
  const spec = readFileSync(resolve(backendIndicators, "spec.py"), "utf8");
  const backend = parseTalibSpecs(talib);

  it("parses the backend catalog (sanity check on the parser itself)", () => {
    expect(backend.size).toBeGreaterThanOrEqual(11);
    expect(backend.get("MACD")).toEqual({
      params: ["fastperiod", "slowperiod", "signalperiod"],
      outputs: ["macd", "signal", "hist"],
    });
    expect(backend.get("OBV")).toEqual({ params: [], outputs: ["value"] });
  });

  it("pins INDICATOR_REGISTRY_VERSION to backend REGISTRY_VERSION", () => {
    const version = /REGISTRY_VERSION\s*=\s*"([^"]+)"/.exec(spec)?.[1];
    expect(version).toBe(INDICATOR_REGISTRY_VERSION);
  });

  it("mirrors exactly the backend indicator ids (no frontend-only or missing ids)", () => {
    const frontendIds = DEFAULT_OVERLAY_DEFINITIONS.map((d) => d.id).sort();
    expect(frontendIds).toEqual([...backend.keys()].sort());
  });

  it.each(DEFAULT_OVERLAY_DEFINITIONS.map((d) => [d.id, d] as const))(
    "%s keeps backend param and output names in backend order",
    (id, definition) => {
      const py = backend.get(id);
      expect(py, `backend spec for ${id}`).toBeDefined();
      expect([...definition.params]).toEqual(py?.params);
      expect(definition.outputs.map((o) => o.name)).toEqual(py?.outputs);
    },
  );

  it("default registry resolves every backend id with a valid pane", () => {
    const registry = createDefaultOverlayRegistry();
    for (const id of backend.keys()) {
      const entry = registry.resolve(id);
      if (entry.placement === "main-overlay") expect(entry.paneIndex).toBe(MAIN_PANE_INDEX);
      else expect(entry.paneIndex).toBeGreaterThanOrEqual(1);
    }
    const subPanes = DEFAULT_OVERLAY_DEFINITIONS.filter((d) => d.placement === "sub-pane").length;
    expect(registry.paneCount).toBe(1 + subPanes);
  });
});
