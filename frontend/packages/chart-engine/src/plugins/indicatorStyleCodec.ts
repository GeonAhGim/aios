/**
 * CH-11/CH-15 — `IndicatorStyle` JSON codec (persisted via CH-8
 * `layout/persistence.ts` layoutState payloads), moved out of
 * `indicatorPlugin.ts` (P6 300-line split, pure move, re-exported from
 * there so `plugins/indicatorPlugin.ts`'s public import path is unchanged).
 */

import { IndicatorPluginError, type IndicatorStyle } from "./indicatorPlugin";

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Plain-JSON-safe encoding for persistence (CH-8 `layout/persistence.ts` layoutState payloads). */
export function encodeIndicatorStyle(style: IndicatorStyle): Record<string, unknown> {
  return {
    outputs: style.outputs.map((o) => ({
      output: o.output,
      color: o.color,
      lineWidth: o.lineWidth,
      visible: o.visible,
      ...(o.upColor !== undefined ? { upColor: o.upColor } : {}),
      ...(o.downColor !== undefined ? { downColor: o.downColor } : {}),
      ...(o.aboveColor !== undefined ? { aboveColor: o.aboveColor } : {}),
      ...(o.belowColor !== undefined ? { belowColor: o.belowColor } : {}),
    })),
  };
}

function decodeOptionalColor(item: Record<string, unknown>, field: string, index: number): string | undefined {
  const value = item[field];
  if (value === undefined) return undefined;
  if (typeof value !== "string") {
    throw new IndicatorPluginError("INDICATOR_PLUGIN_INVALID", "(style)", `malformed style output at index ${index}: "${field}" must be a string`);
  }
  return value;
}

// Fail-closed, no silent fallback (matches OverlayRegistryError/PaneModelError
// convention) — a malformed persisted style throws rather than rendering with
// guessed defaults.
export function decodeIndicatorStyle(raw: unknown): IndicatorStyle {
  if (!isPlainObject(raw) || !Array.isArray(raw.outputs)) {
    throw new IndicatorPluginError("INDICATOR_PLUGIN_INVALID", "(style)", "malformed style document: missing outputs array");
  }
  const outputs = raw.outputs.map((item, index) => {
    if (
      !isPlainObject(item) ||
      typeof item.output !== "string" ||
      typeof item.color !== "string" ||
      typeof item.lineWidth !== "number" ||
      typeof item.visible !== "boolean"
    ) {
      throw new IndicatorPluginError("INDICATOR_PLUGIN_INVALID", "(style)", `malformed style output at index ${index}`);
    }
    return {
      output: item.output,
      color: item.color,
      lineWidth: item.lineWidth,
      visible: item.visible,
      upColor: decodeOptionalColor(item, "upColor", index),
      downColor: decodeOptionalColor(item, "downColor", index),
      aboveColor: decodeOptionalColor(item, "aboveColor", index),
      belowColor: decodeOptionalColor(item, "belowColor", index),
    };
  });
  return { outputs };
}
