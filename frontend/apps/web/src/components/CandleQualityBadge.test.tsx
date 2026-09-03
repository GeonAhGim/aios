import "@testing-library/jest-dom/vitest";
import type { ParsedCandleSeries, ParsedQualityVerdict } from "@aios/shared-types";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CandleQualityBadge } from "./CandleQualityBadge";

// vitest.config에 globals:true가 없어 testing-library의 자동 cleanup 등록이
// 동작하지 않는다(DataFreshness.test.tsx와 동일 사유) — render를 여러 번 호출하며
// 부재(not.toBeInTheDocument)를 검증할 때 명시적으로 cleanup하지 않으면 이전
// 테스트의 DOM이 남아 오탐이 난다.
afterEach(cleanup);

const KEY = { venue: "BITGET" as const, instrument_id: "i-1", timeframe: "1h" as const };

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

function series(overrides: { gaps?: [string, string][] } = {}): ParsedCandleSeries {
  return {
    kind: "ok",
    value: {
      key: KEY,
      candles: [CANDLE],
      gaps: overrides.gaps ?? [],
      adjustment: "RAW",
      as_of: "2026-09-03T05:00:00Z",
      series_hash: "deadbeef",
    },
  };
}

function acceptVerdict(): ParsedQualityVerdict {
  return { kind: "ok", value: { verdict: "ACCEPT", accepted: 24, quarantined: 0, rejected: 0, issues: [] } };
}

function partialVerdict(): ParsedQualityVerdict {
  return {
    kind: "ok",
    value: {
      verdict: "PARTIAL",
      accepted: 22,
      quarantined: 2,
      rejected: 0,
      issues: [{ type: "GAP", severity: "WARN", open_time: "2026-09-03T02:00:00Z", detail: {} }],
    },
  };
}

describe("CandleQualityBadge", () => {
  it("gaps가 없고 verdict가 ACCEPT면 정상 상태를 보여준다", () => {
    render(<CandleQualityBadge series={series()} verdict={acceptVerdict()} />);

    expect(screen.getByTestId("gap-badge")).toHaveTextContent("갭 없음");
    expect(screen.getByTestId("verdict-badge")).toHaveTextContent("품질 정상");
  });

  it("gaps가 있으면 개수와 함께 숨기지 않고 노출한다", () => {
    render(<CandleQualityBadge series={series({ gaps: [["2026-09-03T02:00:00Z", "2026-09-03T04:00:00Z"]] })} />);

    expect(screen.getByTestId("gap-badge")).toHaveTextContent("갭 1건");
  });

  it("verdict!=ACCEPT면 사유(QualityIssueType)와 함께 노출한다", () => {
    render(<CandleQualityBadge series={series()} verdict={partialVerdict()} />);

    const badge = screen.getByTestId("verdict-badge");
    expect(badge).toHaveTextContent("PARTIAL");
    expect(badge).toHaveTextContent("GAP(WARN)");
  });

  it("negative: series가 unsupported_schema_version이면 사유를 노출하고 조용히 숨기지 않는다", () => {
    render(<CandleQualityBadge series={{ kind: "unsupported_schema_version", received: "v2" }} />);

    expect(screen.getByTestId("candle-quality-badge")).toHaveTextContent("v2");
  });

  it("negative: series가 invalid면 해석 불가 문구를 노출한다", () => {
    render(<CandleQualityBadge series={{ kind: "invalid" }} />);

    expect(screen.getByTestId("candle-quality-badge")).toHaveTextContent("해석할 수 없습니다");
  });

  it("negative: verdict가 invalid면 판정 해석 불가 문구를 노출한다", () => {
    render(<CandleQualityBadge series={series()} verdict={{ kind: "invalid" }} />);

    expect(screen.getByTestId("candle-quality-badge")).toHaveTextContent("품질 판정을 해석할 수 없습니다");
  });
});
