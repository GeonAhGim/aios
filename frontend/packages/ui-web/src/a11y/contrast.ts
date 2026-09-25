const HEX_RE = /^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

function hexToRgb(hex: string): [number, number, number] {
  const match = HEX_RE.exec(hex);
  if (!match) throw new Error(`invalid hex color: ${hex}`);
  const clean = match[1]!;
  const full =
    clean.length === 3
      ? clean
          .split("")
          .map((c) => c + c)
          .join("")
      : clean;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)) as [number, number, number];
}

function toLinear(channel: number): number {
  const c = channel / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

/** WCAG 상대 휘도(0~1). */
export function relativeLuminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map(toLinear);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG 2.x 명암비 공식 (밝은 쪽+0.05)/(어두운 쪽+0.05) -- 순서 무관, 항상 1 이상. */
export function contrastRatio(hexA: string, hexB: string): number {
  const [lighter, darker] = [relativeLuminance(hexA), relativeLuminance(hexB)].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
}

export const WCAG_AA_TEXT = 4.5;
export const WCAG_AA_LARGE_TEXT = 3;

export function meetsWcagAA(foregroundHex: string, backgroundHex: string, isLargeText = false): boolean {
  return contrastRatio(foregroundHex, backgroundHex) >= (isLargeText ? WCAG_AA_LARGE_TEXT : WCAG_AA_TEXT);
}
