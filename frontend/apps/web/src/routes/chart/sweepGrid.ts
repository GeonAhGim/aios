import type { SweepAxisInput, SweepComboInput } from "@aios/api-client";

// Keep the interactive grid bounded before compiling or sending any request.
export function materializeSweepGrid(text: string, source: string): {
  axes: SweepAxisInput[];
  combos: Omit<SweepComboInput, "scriptHash">[];
} {
  const grid: unknown = JSON.parse(text);
  if (!grid || typeof grid !== "object" || Array.isArray(grid)) throw new Error("Invalid grid");
  const entries = Object.entries(grid);
  if (entries.length === 0 || entries.length > 2) throw new Error("Invalid axes");
  const axes: SweepAxisInput[] = [];
  let combos: Omit<SweepComboInput, "scriptHash">[] = [{ comboKey: "", axisValues: {}, scriptSource: source }];
  for (const [name, values] of entries) {
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) || !Array.isArray(values) || values.length === 0 ||
      !values.every((v: unknown) => typeof v === "number" && Number.isSafeInteger(v)) ||
      new Set(values).size !== values.length || combos.length * values.length > 64) throw new Error("Invalid values");
    const declaration = new RegExp(`^(\\s*input ${name}\\s*:\\s*int\\s*=\\s*)-?\\d+(\\s*(?://[^\\n]*)?)$`, "gm");
    if ([...source.matchAll(declaration)].length !== 1) throw new Error("Missing integer input");
    const sorted = [...values].sort((a: number, b: number) => a - b) as number[];
    axes.push({ name, values: sorted });
    combos = combos.flatMap((combo) => sorted.map((value) => ({
      comboKey: [combo.comboKey, `${name}=${value}`].filter(Boolean).join(","),
      axisValues: { ...combo.axisValues, [name]: value },
      scriptSource: combo.scriptSource.replace(declaration, (_match, prefix: string, suffix: string) => `${prefix}${value}${suffix}`),
    })));
  }
  return { axes, combos };
}
