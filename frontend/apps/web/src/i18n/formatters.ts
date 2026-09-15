// UX-1 (task-2685): locale-aware formatters for the i18n framework. Money values are
// Decimal strings end to end (walletBalance.ts §L4 LC-16 invariant: "금액은 어디서도
// Number/parseFloat을 거치지 않는다") -- this module extends that discipline to
// display formatting, so a huge or high-precision amount never round-trips through a
// 64-bit float. Grouping/decimal separators are still locale-correct: they come from
// Intl.NumberFormat sampled on a fixed literal (1234.5), never from the caller's
// actual amount, so locale awareness costs nothing in precision.

export interface DecimalParts {
  negative: boolean;
  intPart: string;
  fracPart: string;
}

const DECIMAL_STRING_RE = /^(-)?(\d+)(?:\.(\d+))?$/;

export class InvalidDecimalError extends Error {
  constructor(value: unknown) {
    super(`값이 유효한 Decimal 문자열이 아닙니다: ${JSON.stringify(value)}`);
    this.name = "InvalidDecimalError";
  }
}

export class InvalidDateError extends Error {
  constructor(value: unknown) {
    super(`값이 유효한 날짜가 아닙니다: ${JSON.stringify(value)}`);
    this.name = "InvalidDateError";
  }
}

/** Parses a Decimal string into sign/integer/fraction parts. Throws on anything that isn't one -- including a raw `number`, which would already have lost precision before this function ever saw it. */
export function parseDecimalParts(value: unknown): DecimalParts {
  if (typeof value !== "string") throw new InvalidDecimalError(value);
  const match = DECIMAL_STRING_RE.exec(value);
  if (!match) throw new InvalidDecimalError(value);
  return { negative: match[1] === "-", intPart: match[2], fracPart: match[3] ?? "" };
}

function isZeroParts(parts: DecimalParts): boolean {
  return /^0+$/.test(parts.intPart) && (parts.fracPart === "" || /^0+$/.test(parts.fracPart));
}

/**
 * Rounds to `maxFrac` fraction digits using half-up rounding on a BigInt-scaled
 * integer -- never a float. `maxFrac` >= current fraction length is a no-op.
 */
export function roundDecimalParts(parts: DecimalParts, maxFrac: number): DecimalParts {
  if (parts.fracPart.length <= maxFrac) return parts;
  const keep = parts.fracPart.slice(0, maxFrac);
  const nextDigit = parts.fracPart.charCodeAt(maxFrac) - 48;
  let magnitude = BigInt(parts.intPart + keep || "0");
  if (nextDigit >= 5) magnitude += 1n;
  const combined = magnitude.toString().padStart(maxFrac + 1, "0");
  const newFrac = maxFrac > 0 ? combined.slice(-maxFrac) : "";
  const newInt = maxFrac > 0 ? combined.slice(0, -maxFrac) || "0" : combined;
  return { negative: parts.negative, intPart: newInt, fracPart: newFrac };
}

function groupIntPart(intPart: string, groupSep: string): string {
  if (!groupSep) return intPart;
  return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, groupSep);
}

const separatorCache = new Map<string, { group: string; decimal: string }>();

/** Locale grouping/decimal separator characters, sampled once per locale off a fixed literal (never the caller's amount). */
export function localeSeparators(locale: string): { group: string; decimal: string } {
  const cached = separatorCache.get(locale);
  if (cached) return cached;
  let group = ",";
  let decimal = ".";
  try {
    const parts = new Intl.NumberFormat(locale).formatToParts(1234.5);
    for (const part of parts) {
      if (part.type === "group") group = part.value;
      if (part.type === "decimal") decimal = part.value;
    }
  } catch {
    // unsupported locale tag -- fall back to the en-US-style separators above
  }
  separatorCache.set(locale, { group, decimal });
  return { group, decimal };
}

interface CurrencyDisplay {
  symbol: string;
  position: "prefix" | "suffix";
}

// ISO 4217 fiat only. Anything else (BTC, ETH, ...) suffixes the raw ticker --
// Intl's currency formatter does not know non-ISO codes and we don't want a silent
// misformat for crypto amounts.
const CURRENCY_SYMBOLS: Record<string, CurrencyDisplay> = {
  KRW: { symbol: "₩", position: "prefix" },
  USD: { symbol: "$", position: "prefix" },
  EUR: { symbol: "€", position: "prefix" },
  JPY: { symbol: "¥", position: "prefix" },
};

function applyCurrency(numeric: string, currency?: string): string {
  if (!currency) return numeric;
  const known = CURRENCY_SYMBOLS[currency.toUpperCase()];
  if (!known) return `${numeric} ${currency.toUpperCase()}`;
  return known.position === "prefix" ? `${known.symbol}${numeric}` : `${numeric}${known.symbol}`;
}

export interface FormatMoneyOptions {
  /** ISO 4217 code (KRW/USD/EUR/JPY get a symbol) or a crypto ticker (suffixed as-is). */
  currency?: string;
  /** BCP 47 locale tag for grouping/decimal separators. Defaults to "ko-KR". */
  locale?: string;
  minFractionDigits?: number;
  maxFractionDigits?: number;
}

/**
 * Formats a Decimal string amount for display without ever converting it to a JS
 * `number`. Throws `InvalidDecimalError` on malformed input rather than silently
 * coercing (e.g. via `Number(value) || 0`), because a coerced "0" would misrepresent
 * an unparseable amount as a real zero balance.
 */
export function formatMoney(value: string, options: FormatMoneyOptions = {}): string {
  const parts = parseDecimalParts(value);
  const locale = options.locale ?? "ko-KR";
  const { group, decimal } = localeSeparators(locale);
  const maxFrac = options.maxFractionDigits ?? Math.max(parts.fracPart.length, options.minFractionDigits ?? 0);
  const minFrac = Math.min(options.minFractionDigits ?? 0, maxFrac);

  const rounded = roundDecimalParts(parts, maxFrac);
  const grouped = groupIntPart(rounded.intPart, group);
  const frac = rounded.fracPart.padEnd(minFrac, "0");
  const sign = rounded.negative && !isZeroParts(rounded) ? "-" : "";
  const numeric = frac.length > 0 ? `${sign}${grouped}${decimal}${frac}` : `${sign}${grouped}`;
  return applyCurrency(numeric, options.currency);
}

/** Formats a plain (non-money) Decimal-safe integer/decimal string with locale grouping, e.g. share counts. */
export function formatNumber(value: string, options: { locale?: string } = {}): string {
  const parts = parseDecimalParts(value);
  const { group, decimal } = localeSeparators(options.locale ?? "ko-KR");
  const grouped = groupIntPart(parts.intPart, group);
  const sign = parts.negative && !isZeroParts(parts) ? "-" : "";
  return parts.fracPart ? `${sign}${grouped}${decimal}${parts.fracPart}` : `${sign}${grouped}`;
}

/** Formats an ISO-8601 timestamp for display. Dates carry no Decimal-precision concern -- Intl.DateTimeFormat is safe here. */
export function formatDate(
  value: string | Date,
  options: { locale?: string; dateStyle?: Intl.DateTimeFormatOptions["dateStyle"]; timeStyle?: Intl.DateTimeFormatOptions["timeStyle"] } = {},
): string {
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) throw new InvalidDateError(value);
  const locale = options.locale ?? "ko-KR";
  return new Intl.DateTimeFormat(locale, {
    dateStyle: options.dateStyle ?? "medium",
    timeStyle: options.timeStyle,
  }).format(date);
}
