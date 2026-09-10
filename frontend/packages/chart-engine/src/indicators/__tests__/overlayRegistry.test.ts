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
  type OverlayPlacement,
} from "../overlayRegistry";

const sma: OverlayDefinition = { id: "SMA", placement: "main-overlay", params: ["timeperiod"], outputs: [{ name: "value", series: "line" }] };
const rsi: OverlayDefinition = { id: "RSI", placement: "sub-pane", params: ["timeperiod"], outputs: [{ name: "value", series: "line" }] };
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
const talibSource = readFileSync(resolve(backendIndicators, "specs_talib.py"), "utf8");
const specSource = readFileSync(resolve(backendIndicators, "spec.py"), "utf8");

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

const backendSpecs = parseTalibSpecs(talibSource);

/** Empty result = no drift. Factored out so it can run against synthetic, not just real, inputs. */
function checkDrift(frontend: readonly OverlayDefinition[], backend: ReadonlyMap<string, PySpec>): string[] {
  const reasons: string[] = [];
  const frontendIds = frontend.map((d) => d.id).sort();
  const backendIds = [...backend.keys()].sort();
  if (JSON.stringify(frontendIds) !== JSON.stringify(backendIds)) {
    reasons.push(`id set mismatch: frontend=[${frontendIds}] backend=[${backendIds}]`);
  }
  for (const def of frontend) {
    const py = backend.get(def.id);
    if (!py) continue;
    if (JSON.stringify([...def.params]) !== JSON.stringify(py.params)) reasons.push(`${def.id} param mismatch`);
    const outputs = def.outputs.map((o) => o.name);
    if (JSON.stringify(outputs) !== JSON.stringify(py.outputs)) reasons.push(`${def.id} output mismatch`);
  }
  return reasons;
}

describe("drift guard against backend src/core/indicators (SSOT)", () => {
  it("parses the backend catalog (sanity check on the parser itself)", () => {
    expect(backendSpecs.size).toBeGreaterThanOrEqual(11);
    expect(backendSpecs.get("MACD")).toEqual({
      params: ["fastperiod", "slowperiod", "signalperiod"],
      outputs: ["macd", "signal", "hist"],
    });
    expect(backendSpecs.get("OBV")).toEqual({ params: [], outputs: ["value"] });
  });

  it("pins INDICATOR_REGISTRY_VERSION to backend REGISTRY_VERSION", () => {
    const version = /REGISTRY_VERSION\s*=\s*"([^"]+)"/.exec(specSource)?.[1];
    expect(version).toBe(INDICATOR_REGISTRY_VERSION);
  });

  it("mirrors exactly the backend indicator ids (no frontend-only or missing ids)", () => {
    expect(checkDrift(DEFAULT_OVERLAY_DEFINITIONS, backendSpecs)).toEqual([]);
  });

  it.each(DEFAULT_OVERLAY_DEFINITIONS.map((d) => [d.id, d] as const))(
    "%s keeps backend param and output names in backend order",
    (id, definition) => {
      const py = backendSpecs.get(id);
      expect(py, `backend spec for ${id}`).toBeDefined();
      expect([...definition.params]).toEqual(py?.params);
      expect(definition.outputs.map((o) => o.name)).toEqual(py?.outputs);
    },
  );

  it("default registry resolves every backend id with a valid pane", () => {
    const registry = createDefaultOverlayRegistry();
    for (const id of backendSpecs.keys()) {
      const entry = registry.resolve(id);
      if (entry.placement === "main-overlay") expect(entry.paneIndex).toBe(MAIN_PANE_INDEX);
      else expect(entry.paneIndex).toBeGreaterThanOrEqual(1);
    }
    const subPanes = DEFAULT_OVERLAY_DEFINITIONS.filter((d) => d.placement === "sub-pane").length;
    expect(registry.paneCount).toBe(1 + subPanes);
  });
});

// --- Drift guard failure injection (synthetic corruption, not just real-file diffing) ---

const renameParam = (s: string) => s.replace('_period("timeperiod"', '_period("time_window"');
const addUnknownIndicator = (s: string) =>
  `${s}\n    "VWAP": IndicatorSpec(\n        name="VWAP",\n        outputs=("value",),\n    ),`;

const BACKEND_CORRUPTIONS: ReadonlyArray<readonly [string, (s: string) => string, string]> = [
  ["renames SMA timeperiod -> time_window", renameParam, "SMA param mismatch"],
  ["adds a VWAP indicator the frontend never registered", addUnknownIndicator, "id set mismatch"],
];

describe("drift guard failure injection (synthetic backend/frontend divergence)", () => {
  it.each(BACKEND_CORRUPTIONS)("goes red when a synthetic backend %s", (_label, corrupt, expectedPrefix) => {
    const corruptedSource = corrupt(talibSource);
    expect(corruptedSource).not.toBe(talibSource);
    const reasons = checkDrift(DEFAULT_OVERLAY_DEFINITIONS, parseTalibSpecs(corruptedSource));
    expect(reasons.some((r) => r.startsWith(expectedPrefix))).toBe(true);
  });

  it("goes red when the frontend catalog itself drifts from the real backend (MACD output renamed)", () => {
    const corruptedFrontend = DEFAULT_OVERLAY_DEFINITIONS.map((d) =>
      d.id === "MACD" ? { ...d, outputs: [{ ...d.outputs[0], name: "macd_line" }, ...d.outputs.slice(1)] } : d,
    );
    const reasons = checkDrift(corruptedFrontend, backendSpecs);
    expect(reasons.some((r) => r.startsWith("MACD output mismatch"))).toBe(true);
  });

  it("stays green against the real, unmodified backend (control proving the guard isn't always red)", () => {
    expect(checkDrift(DEFAULT_OVERLAY_DEFINITIONS, backendSpecs)).toEqual([]);
  });
});

// --- Numeric performance assertion ------------------------------------------
describe("performance", () => {
  it("registers and resolves 2000 overlays within a fixed ms budget", () => {
    const many: OverlayDefinition[] = Array.from({ length: 2000 }, (_, i) => ({
      id: `IND_${i}`,
      placement: i % 2 === 0 ? "main-overlay" : "sub-pane",
      params: ["timeperiod"],
      outputs: [{ name: "value", series: "line" }],
    }));
    const start = performance.now();
    const registry = createOverlayRegistry(many);
    for (const def of many) registry.resolve(def.id);
    const elapsedMs = performance.now() - start;
    expect(registry.list()).toHaveLength(2000);
    expect(registry.paneCount).toBe(1001);
    expect(elapsedMs).toBeLessThan(500);
  });
});

// --- D3: adversarial input, multi-instance isolation, seeded replay ---------

function mulberry32(seed: number): () => number {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const ADVERSARIAL_IDS = ["__proto__", "constructor", "prototype", "  ", "A".repeat(500), "SMA​"];

describe("D3 — adversarial input, multi-instance isolation, seeded replay", () => {
  it("isolates state across concurrently live registry instances", () => {
    const a = createOverlayRegistry([sma]);
    const b = createOverlayRegistry([rsi]);
    expect(a.has("RSI")).toBe(false);
    expect(b.has("SMA")).toBe(false);
    a.register(macd);
    a.register({ ...rsi, id: "ATR" });
    expect(b.has("MACD")).toBe(false);
    expect(b.has("ATR")).toBe(false);
    expect([a.paneCount, b.paneCount]).toEqual([3, 2]);
  });

  it.each(ADVERSARIAL_IDS)("accepts adversarial id %j as an opaque string without prototype pollution", (id) => {
    const registry = createOverlayRegistry();
    const outputs = [{ name: "value", series: "line" as const }];
    const entry = registry.register({ id, placement: "main-overlay", params: [], outputs });
    expect(entry.id).toBe(id);
    expect(registry.resolve(id)).toEqual(entry);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  it("replays 200 seeded random operation sequences across fresh instances without invariant violations", () => {
    const rand = mulberry32(20260910);
    for (let replay = 0; replay < 200; replay++) {
      const registry = createOverlayRegistry();
      const ids = new Set<string>();
      for (let i = 0, n = 1 + Math.floor(rand() * 8); i < n; i++) {
        const id = `IND_${Math.floor(rand() * 4)}`;
        const placement: OverlayPlacement = rand() < 0.5 ? "main-overlay" : "sub-pane";
        const wasKnown = ids.has(id);
        try {
          registry.register({ id, placement, params: [], outputs: [{ name: "value", series: "line" }] });
          expect(wasKnown).toBe(false);
          ids.add(id);
        } catch (e) {
          expect(e).toBeInstanceOf(OverlayRegistryError);
          expect(wasKnown).toBe(true);
        }
      }
      expect(registry.list()).toHaveLength(ids.size);
    }
  });
});
