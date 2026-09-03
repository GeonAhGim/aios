import { describe, expect, it } from "vitest";
import { parseCandleSeries, parseQualityVerdict } from "./candleSeries";

const KEY = { venue: "BITGET", instrument_id: "i-1", timeframe: "1h" };

const CANDLE = {
  key: KEY,
  open_time: "2026-09-03T00:00:00Z",
  close_time: "2026-09-03T01:00:00Z",
  open: "50000.00",
  high: "50500.00",
  low: "49800.00",
  close: "50200.00",
  volume: "12.5",
  quote_volume: "628500.00",
};

const SERIES = {
  key: KEY,
  candles: [CANDLE],
  gaps: [["2026-09-03T02:00:00Z", "2026-09-03T04:00:00Z"]],
  adjustment: "RAW",
  as_of: "2026-09-03T05:00:00Z",
  series_hash: "deadbeef",
  schema_version: "v1",
};

const ISSUE = {
  type: "GAP",
  severity: "WARN",
  open_time: "2026-09-03T02:00:00Z",
  detail: { expected: "2026-09-03T02:00:00Z" },
};

const ACCEPT_VERDICT = {
  verdict: "ACCEPT",
  accepted: 24,
  quarantined: 0,
  rejected: 0,
  issues: [],
  schema_version: "v1",
};

const PARTIAL_VERDICT = {
  verdict: "PARTIAL",
  accepted: 22,
  quarantined: 2,
  rejected: 0,
  issues: [ISSUE],
  schema_version: "v1",
};

describe("parseCandleSeries", () => {
  it("§3.1 CandleSeries 필드를 문자열 Decimal·UTC ISO 그대로 보존한다", () => {
    const parsed = parseCandleSeries(SERIES);
    expect(parsed).toEqual({ kind: "ok", value: SERIES });
    if (parsed.kind === "ok") {
      expect(typeof parsed.value.candles[0]!.open).toBe("string");
      expect(typeof parsed.value.candles[0]!.volume).toBe("string");
    }
  });

  it("ApiResponse 봉투({data})로 감싼 응답도 파싱한다", () => {
    const parsed = parseCandleSeries({ data: SERIES, meta: { trace_id: "t1" } });
    expect(parsed).toEqual({ kind: "ok", value: SERIES });
  });

  it("gaps가 비어있는 정상 시리즈도 파싱한다", () => {
    const noGaps = { ...SERIES, gaps: [] };
    expect(parseCandleSeries(noGaps)).toEqual({ kind: "ok", value: noGaps });
  });

  it("negative: 미지의 timeframe이면 invalid이다", () => {
    const badTimeframe = { ...SERIES, key: { ...KEY, timeframe: "2h" } };
    expect(parseCandleSeries(badTimeframe)).toEqual({ kind: "invalid" });
  });

  it("negative: gaps 구간이 역순(시작>=끝)이면 invalid이다", () => {
    const reversedGap = { ...SERIES, gaps: [["2026-09-03T04:00:00Z", "2026-09-03T02:00:00Z"]] };
    expect(parseCandleSeries(reversedGap)).toEqual({ kind: "invalid" });
  });

  it("negative: quote_volume이 숫자 타입이면(Decimal이 Number로 샌 경우) invalid이다", () => {
    const numericVolume = { ...SERIES, candles: [{ ...CANDLE, quote_volume: 628500 }] };
    expect(parseCandleSeries(numericVolume)).toEqual({ kind: "invalid" });
  });

  it("negative: schema_version이 v1이 아니면 예외 없이 unsupported_schema_version을 반환한다", () => {
    expect(parseCandleSeries({ ...SERIES, schema_version: "v2" })).toEqual({
      kind: "unsupported_schema_version",
      received: "v2",
    });
  });

  it("negative: schema_version 필드가 아예 없으면 unsupported_schema_version(received=undefined)을 반환한다", () => {
    const { schema_version: _drop, ...withoutVersion } = SERIES;
    expect(parseCandleSeries(withoutVersion)).toEqual({
      kind: "unsupported_schema_version",
      received: undefined,
    });
  });

  it("negative: 응답이 없으면(null/undefined) invalid이고 예외를 던지지 않는다", () => {
    expect(() => parseCandleSeries(null)).not.toThrow();
    expect(parseCandleSeries(null)).toEqual({ kind: "invalid" });
    expect(parseCandleSeries(undefined)).toEqual({ kind: "invalid" });
  });
});

describe("parseQualityVerdict", () => {
  it("§3.1 QualityVerdict(ACCEPT) 필드를 그대로 보존한다", () => {
    expect(parseQualityVerdict(ACCEPT_VERDICT)).toEqual({ kind: "ok", value: ACCEPT_VERDICT });
  });

  it("§3.1 QualityVerdict(PARTIAL, issues 포함)를 그대로 보존한다", () => {
    expect(parseQualityVerdict(PARTIAL_VERDICT)).toEqual({ kind: "ok", value: PARTIAL_VERDICT });
  });

  it("negative: quarantined>0인데 verdict=ACCEPT인 모순 입력은 invalid이다", () => {
    const contradictory = { ...ACCEPT_VERDICT, quarantined: 2 };
    expect(parseQualityVerdict(contradictory)).toEqual({ kind: "invalid" });
  });

  it("negative: rejected>0인데 verdict=ACCEPT인 모순 입력은 invalid이다", () => {
    const contradictory = { ...ACCEPT_VERDICT, rejected: 1 };
    expect(parseQualityVerdict(contradictory)).toEqual({ kind: "invalid" });
  });

  it("negative: 화이트리스트 밖 verdict 값은 invalid이다", () => {
    const badVerdict = { ...ACCEPT_VERDICT, verdict: "APPROVED" };
    expect(parseQualityVerdict(badVerdict)).toEqual({ kind: "invalid" });
  });

  it("negative: issues의 severity가 화이트리스트 밖이면 invalid이다", () => {
    const badSeverity = { ...PARTIAL_VERDICT, issues: [{ ...ISSUE, severity: "CRITICAL" }] };
    expect(parseQualityVerdict(badSeverity)).toEqual({ kind: "invalid" });
  });

  it("negative: accepted가 음수면 invalid이다", () => {
    const negativeCount = { ...ACCEPT_VERDICT, accepted: -1 };
    expect(parseQualityVerdict(negativeCount)).toEqual({ kind: "invalid" });
  });

  it("negative: schema_version이 다르면 unsupported_schema_version이다", () => {
    expect(parseQualityVerdict({ ...ACCEPT_VERDICT, schema_version: "v0" })).toEqual({
      kind: "unsupported_schema_version",
      received: "v0",
    });
  });
});
