import { describe, expect, it } from "vitest";
import { parseInstrumentView, parseSymbolAlias } from "./instrumentView";

const INSTRUMENT = {
  instrument_id: "i-1",
  venue: "BITGET",
  canonical_symbol: "BTC/USDT",
  venue_symbol: "BTCUSDT",
  asset_class: "CRYPTO",
  base: "BTC",
  quote: "USDT",
  tick_size: "0.01",
  lot_size: "0.0001",
  status: "LISTED",
  listed_at: "2026-01-01T00:00:00Z",
  delisted_at: null,
  schema_version: "v1",
};

const ALIAS = {
  alias_id: "a-1",
  instrument_id: "i-1",
  venue: "BITGET",
  alias_symbol: "XBTUSDT",
  valid_from: "2026-01-01T00:00:00Z",
  valid_to: "2026-06-01T00:00:00Z",
};

describe("parseInstrumentView", () => {
  it("§3.1 InstrumentRef 필드를 문자열 Decimal 그대로 보존한다", () => {
    expect(parseInstrumentView(INSTRUMENT)).toEqual({ kind: "ok", value: INSTRUMENT });
  });

  it("ApiResponse 봉투({data})로 감싼 응답도 파싱한다", () => {
    const parsed = parseInstrumentView({ data: INSTRUMENT, meta: { trace_id: "t1" } });
    expect(parsed).toEqual({ kind: "ok", value: INSTRUMENT });
  });

  it("DELISTED 상태·delisted_at 포함 인스트루먼트도 파싱한다", () => {
    const delisted = { ...INSTRUMENT, status: "DELISTED", delisted_at: "2026-08-01T00:00:00Z" };
    expect(parseInstrumentView(delisted)).toEqual({ kind: "ok", value: delisted });
  });

  it("base/quote가 null인 인스트루먼트(비크립토)도 파싱한다", () => {
    const noBaseQuote = { ...INSTRUMENT, base: null, quote: null };
    expect(parseInstrumentView(noBaseQuote)).toEqual({ kind: "ok", value: noBaseQuote });
  });

  it("negative: 화이트리스트 밖 status 문자열은 invalid이다(§4.2)", () => {
    const badStatus = { ...INSTRUMENT, status: "ACTIVE" };
    expect(parseInstrumentView(badStatus)).toEqual({ kind: "invalid" });
  });

  it("negative: tick_size가 숫자 타입이면(Decimal이 Number로 샌 경우) invalid이다", () => {
    const numericTickSize = { ...INSTRUMENT, tick_size: 0.01 };
    expect(parseInstrumentView(numericTickSize)).toEqual({ kind: "invalid" });
  });

  it("negative: lot_size가 숫자 타입이면 invalid이다", () => {
    const numericLotSize = { ...INSTRUMENT, lot_size: 0.0001 };
    expect(parseInstrumentView(numericLotSize)).toEqual({ kind: "invalid" });
  });

  it("negative: schema_version이 v1이 아니면 예외 없이 unsupported_schema_version을 반환한다", () => {
    expect(parseInstrumentView({ ...INSTRUMENT, schema_version: "v2" })).toEqual({
      kind: "unsupported_schema_version",
      received: "v2",
    });
  });

  it("negative: schema_version 필드가 아예 없으면 unsupported_schema_version(received=undefined)을 반환한다", () => {
    const { schema_version: _drop, ...withoutVersion } = INSTRUMENT;
    expect(parseInstrumentView(withoutVersion)).toEqual({
      kind: "unsupported_schema_version",
      received: undefined,
    });
  });

  it("negative: 응답이 없으면(null/undefined) invalid이고 예외를 던지지 않는다", () => {
    expect(() => parseInstrumentView(null)).not.toThrow();
    expect(parseInstrumentView(null)).toEqual({ kind: "invalid" });
    expect(parseInstrumentView(undefined)).toEqual({ kind: "invalid" });
  });
});

describe("parseSymbolAlias", () => {
  it("§4.2 SymbolAlias(만료된 별칭, valid_to 포함)를 그대로 보존한다", () => {
    expect(parseSymbolAlias(ALIAS)).toEqual({ kind: "ok", value: ALIAS });
  });

  it("valid_to=null(현재 유효한 별칭)도 파싱한다", () => {
    const current = { ...ALIAS, valid_to: null };
    expect(parseSymbolAlias(current)).toEqual({ kind: "ok", value: current });
  });

  it("negative: valid_to < valid_from(역순 구간)이면 invalid이다", () => {
    const reversed = { ...ALIAS, valid_from: "2026-06-01T00:00:00Z", valid_to: "2026-01-01T00:00:00Z" };
    expect(parseSymbolAlias(reversed)).toEqual({ kind: "invalid" });
  });

  it("negative: valid_to === valid_from(0 길이 구간)이면 invalid이다", () => {
    const zeroLength = { ...ALIAS, valid_to: ALIAS.valid_from };
    expect(parseSymbolAlias(zeroLength)).toEqual({ kind: "invalid" });
  });

  it("negative: 화이트리스트 밖 venue 값은 invalid이다", () => {
    const badVenue = { ...ALIAS, venue: "NASDAQ" };
    expect(parseSymbolAlias(badVenue)).toEqual({ kind: "invalid" });
  });

  it("negative: 응답이 없으면(null/undefined) invalid이고 예외를 던지지 않는다", () => {
    expect(() => parseSymbolAlias(null)).not.toThrow();
    expect(parseSymbolAlias(null)).toEqual({ kind: "invalid" });
    expect(parseSymbolAlias(undefined)).toEqual({ kind: "invalid" });
  });
});
