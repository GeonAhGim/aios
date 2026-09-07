import { describe, expect, it } from "vitest";
import {
  LAYOUT_MODEL_SCHEMA_VERSION,
  LayoutModelError,
  type ChartLayoutModel,
  createEmptyLayoutModel,
  decodeLayoutModel,
  encodeLayoutModel,
} from "./layoutModel";

function expectLayoutError(fn: () => unknown, code: string): LayoutModelError {
  try {
    fn();
  } catch (err) {
    expect(err).toBeInstanceOf(LayoutModelError);
    const layoutErr = err as LayoutModelError;
    expect(layoutErr.code).toBe(code);
    return layoutErr;
  }
  throw new Error("expected LayoutModelError to be thrown");
}

const sample: ChartLayoutModel = {
  schemaVersion: LAYOUT_MODEL_SCHEMA_VERSION,
  panels: [
    {
      id: "p1",
      instrument: { instrumentId: "11111111-1111-1111-1111-111111111111", venue: "BINANCE", symbol: "BTCUSDT" },
      timeframe: "1h",
      indicators: [{ id: "SMA", params: { period: 20 } }, { id: "RSI" }],
      drawingSetId: "p1-drawings",
      objectTreeOrder: ["RSI", "SMA"],
      lockedIndicatorIds: ["SMA"],
    },
    {
      id: "p2",
      instrument: { instrumentId: "22222222-2222-2222-2222-222222222222", venue: "BINANCE", symbol: "ETHUSDT" },
      timeframe: "1d",
      indicators: [],
      drawingSetId: "p2-drawings",
      objectTreeOrder: [],
      lockedIndicatorIds: [],
    },
  ],
  activePanelId: "p1",
  watchlists: [
    {
      id: "w1",
      name: "Majors",
      entries: [{ instrumentId: "11111111-1111-1111-1111-111111111111", venue: "BINANCE", symbol: "BTCUSDT" }],
    },
  ],
};

describe("round trip", () => {
  it("decode(encode(x)) deep-equals x for a multi-panel + watchlist layout", () => {
    const back = decodeLayoutModel(encodeLayoutModel(sample));
    expect(back).toEqual(sample);
    expect(back).not.toBe(sample);
  });

  it("encode(decode(json)) === json (canonical form is a fixed point)", () => {
    const encoded = encodeLayoutModel(sample);
    const text = JSON.stringify(encoded);
    const decoded = decodeLayoutModel(JSON.parse(text));
    expect(JSON.stringify(encodeLayoutModel(decoded))).toBe(text);
  });

  it("createEmptyLayoutModel round-trips (empty panels/watchlists is a valid layout)", () => {
    const empty = createEmptyLayoutModel();
    expect(decodeLayoutModel(encodeLayoutModel(empty))).toEqual(empty);
  });
});

describe("negative: empty/malformed layout", () => {
  it("rejects a genuinely empty object ({}) — missing fields are never defaulted", () => {
    expectLayoutError(() => decodeLayoutModel({}), "CHART_LAYOUT_SCHEMA_UNSUPPORTED");
  });

  it("rejects null/array/non-object documents", () => {
    expectLayoutError(() => decodeLayoutModel(null), "CHART_LAYOUT_FIELD_INVALID");
    expectLayoutError(() => decodeLayoutModel([]), "CHART_LAYOUT_FIELD_INVALID");
    expectLayoutError(() => decodeLayoutModel(42), "CHART_LAYOUT_FIELD_INVALID");
  });

  it.each<[string, unknown]>([
    ["missing schemaVersion", { panels: [], activePanelId: null, watchlists: [] }],
    ["future schemaVersion", { schemaVersion: 2, panels: [], activePanelId: null, watchlists: [] }],
    ["string schemaVersion", { schemaVersion: "1", panels: [], activePanelId: null, watchlists: [] }],
  ])("rejects %s with CHART_LAYOUT_SCHEMA_UNSUPPORTED", (_label, doc) => {
    expectLayoutError(() => decodeLayoutModel(doc), "CHART_LAYOUT_SCHEMA_UNSUPPORTED");
  });

  it("rejects unknown top-level fields", () => {
    expectLayoutError(
      () => decodeLayoutModel({ ...createEmptyLayoutModel(), extra: 1 }),
      "CHART_LAYOUT_FIELD_UNKNOWN",
    );
  });

  it("rejects missing panels/activePanelId/watchlists", () => {
    expectLayoutError(() => decodeLayoutModel({ schemaVersion: 1 }), "CHART_LAYOUT_FIELD_MISSING");
    expectLayoutError(
      () => decodeLayoutModel({ schemaVersion: 1, panels: [] }),
      "CHART_LAYOUT_FIELD_MISSING",
    );
    expectLayoutError(
      () => decodeLayoutModel({ schemaVersion: 1, panels: [], activePanelId: null }),
      "CHART_LAYOUT_FIELD_MISSING",
    );
  });

  it("rejects activePanelId referencing an unknown panel", () => {
    expectLayoutError(
      () => decodeLayoutModel({ schemaVersion: 1, panels: [], activePanelId: "ghost", watchlists: [] }),
      "CHART_LAYOUT_FIELD_INVALID",
    );
    expectLayoutError(
      () => encodeLayoutModel({ ...createEmptyLayoutModel(), activePanelId: "ghost" }),
      "CHART_LAYOUT_FIELD_INVALID",
    );
  });

  it("rejects duplicate panel ids and duplicate watchlist ids", () => {
    const dupPanels: ChartLayoutModel = { ...sample, panels: [sample.panels[0]!, sample.panels[0]!] };
    expectLayoutError(() => encodeLayoutModel(dupPanels), "CHART_LAYOUT_DUPLICATE_ID");

    const dupWatchlists: ChartLayoutModel = { ...sample, watchlists: [sample.watchlists[0]!, sample.watchlists[0]!] };
    expectLayoutError(() => encodeLayoutModel(dupWatchlists), "CHART_LAYOUT_DUPLICATE_ID");
  });

  it("rejects a panel with unknown field / missing instrument / empty timeframe / non-finite indicator param", () => {
    const basePanel = sample.panels[0]!;
    expectLayoutError(
      () => decodeLayoutModel({ ...createEmptyLayoutModel(), panels: [{ ...basePanel, extra: 1 }] }),
      "CHART_LAYOUT_FIELD_UNKNOWN",
    );
    const { instrument: _unused, ...withoutInstrument } = basePanel;
    expectLayoutError(
      () => decodeLayoutModel({ ...createEmptyLayoutModel(), panels: [withoutInstrument] }),
      "CHART_LAYOUT_FIELD_MISSING",
    );
    expectLayoutError(
      () => decodeLayoutModel({ ...createEmptyLayoutModel(), panels: [{ ...basePanel, timeframe: "" }] }),
      "CHART_LAYOUT_FIELD_INVALID",
    );
    expectLayoutError(
      () =>
        decodeLayoutModel({
          ...createEmptyLayoutModel(),
          panels: [{ ...basePanel, indicators: [{ id: "SMA", params: { period: Number.NaN } }] }],
        }),
      "CHART_LAYOUT_FIELD_INVALID",
    );
  });
});

describe("CH-16b: objectTreeOrder/lockedIndicatorIds", () => {
  it("a panel saved before this leaf (missing both fields) decodes with [] defaults, not an error", () => {
    const basePanel = sample.panels[0]!;
    const { objectTreeOrder: _order, lockedIndicatorIds: _locked, ...withoutObjectTreeFields } = basePanel;
    const decoded = decodeLayoutModel({ ...createEmptyLayoutModel(), panels: [withoutObjectTreeFields] });
    expect(decoded.panels[0]!.objectTreeOrder).toEqual([]);
    expect(decoded.panels[0]!.lockedIndicatorIds).toEqual([]);
  });

  it("negative: rejects a non-array or non-string-element objectTreeOrder/lockedIndicatorIds", () => {
    const basePanel = sample.panels[0]!;
    expectLayoutError(
      () => decodeLayoutModel({ ...createEmptyLayoutModel(), panels: [{ ...basePanel, objectTreeOrder: "SMA" }] }),
      "CHART_LAYOUT_FIELD_INVALID",
    );
    expectLayoutError(
      () => decodeLayoutModel({ ...createEmptyLayoutModel(), panels: [{ ...basePanel, lockedIndicatorIds: [1] }] }),
      "CHART_LAYOUT_FIELD_INVALID",
    );
  });
});
