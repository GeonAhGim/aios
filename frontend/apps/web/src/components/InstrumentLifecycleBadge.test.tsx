import "@testing-library/jest-dom/vitest";
import type { InstrumentView, ParsedInstrumentView, SymbolAlias } from "@aios/shared-types";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { InstrumentLifecycleBadge } from "./InstrumentLifecycleBadge";

// vitest.config에 globals:true가 없어 testing-library의 자동 cleanup 등록이
// 동작하지 않는다(CandleQualityBadge.test.tsx와 동일 사유).
afterEach(cleanup);

const NOW = "2026-09-03T00:00:00Z";

const INSTRUMENT: InstrumentView = {
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
};

function ok(overrides: Partial<InstrumentView> = {}): ParsedInstrumentView {
  return { kind: "ok", value: { ...INSTRUMENT, ...overrides } };
}

function currentAlias(): SymbolAlias {
  return {
    alias_id: "a-1",
    instrument_id: "i-1",
    venue: "BITGET",
    alias_symbol: "BTCUSDT",
    valid_from: "2026-01-01T00:00:00Z",
    valid_to: null,
  };
}

function expiredAlias(): SymbolAlias {
  return {
    alias_id: "a-0",
    instrument_id: "i-1",
    venue: "BITGET",
    alias_symbol: "XBTUSDT",
    valid_from: "2026-01-01T00:00:00Z",
    valid_to: "2026-02-01T00:00:00Z",
  };
}

describe("InstrumentLifecycleBadge", () => {
  it("LISTED이고 유효한 별칭이 있으면 활성 배지만 보여준다(확인 필요 없음)", () => {
    render(<InstrumentLifecycleBadge instrument={ok()} aliases={[currentAlias()]} now={NOW} />);

    expect(screen.getByTestId("status-badge")).toHaveTextContent("활성");
    expect(screen.getByTestId("symbol-label")).toHaveTextContent("BTC/USDT (BTCUSDT)");
    expect(screen.queryByTestId("needs-review-badge")).not.toBeInTheDocument();
  });

  it("DELISTED는 상장폐지 배지를 보여주고 확인 필요를 띄우지 않는다", () => {
    render(<InstrumentLifecycleBadge instrument={ok({ status: "DELISTED", delisted_at: NOW })} now={NOW} />);

    expect(screen.getByTestId("status-badge")).toHaveTextContent("상장폐지");
    expect(screen.queryByTestId("needs-review-badge")).not.toBeInTheDocument();
  });

  it("LISTED인데 별칭이 전부 만료됐으면 서버 status를 그대로 두고 확인 필요를 추가로 띄운다", () => {
    render(<InstrumentLifecycleBadge instrument={ok()} aliases={[expiredAlias()]} now={NOW} />);

    expect(screen.getByTestId("status-badge")).toHaveTextContent("활성");
    expect(screen.getByTestId("needs-review-badge")).toHaveTextContent("확인 필요");
  });

  it("LISTED인데 aliases가 비어있으면(이력 미수신) 확인 필요를 임의로 띄우지 않는다", () => {
    render(<InstrumentLifecycleBadge instrument={ok()} now={NOW} />);

    expect(screen.queryByTestId("needs-review-badge")).not.toBeInTheDocument();
  });

  it("negative: instrument가 unsupported_schema_version이면 사유를 노출하고 조용히 숨기지 않는다", () => {
    render(<InstrumentLifecycleBadge instrument={{ kind: "unsupported_schema_version", received: "v2" }} now={NOW} />);

    expect(screen.getByTestId("instrument-lifecycle-badge")).toHaveTextContent("v2");
  });

  it("negative: instrument가 invalid면 해석 불가 문구를 노출한다", () => {
    render(<InstrumentLifecycleBadge instrument={{ kind: "invalid" }} now={NOW} />);

    expect(screen.getByTestId("instrument-lifecycle-badge")).toHaveTextContent("해석할 수 없습니다");
  });
});
