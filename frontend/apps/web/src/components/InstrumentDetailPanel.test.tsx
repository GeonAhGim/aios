import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ApiError } from "@aios/api-client";
import type { InstrumentView, ParsedSymbolAlias } from "@aios/shared-types";
import type { UseQueryResult } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InstrumentDetailPanel } from "./InstrumentDetailPanel";

afterEach(() => cleanup());

function instrument(overrides: Partial<InstrumentView> = {}): InstrumentView {
  return {
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
    ...overrides,
  };
}

function aliasQuery(
  overrides: Partial<UseQueryResult<ParsedSymbolAlias[]>> = {},
): UseQueryResult<ParsedSymbolAlias[]> {
  return {
    data: [],
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  } as unknown as UseQueryResult<ParsedSymbolAlias[]>;
}

// spec §3.3 RESOURCE_NOT_FOUND(404)는 재시도 배너가 아니라 NotFoundState로 렌더한다
// (task-1056에서 배선, task-1089 배치2에서 단위 테스트로 고정).
describe("InstrumentDetailPanel 별칭 조회 404", () => {
  it("negative: 별칭 조회가 RESOURCE_NOT_FOUND(404)면 NotFoundState를 보여준다", () => {
    render(
      <InstrumentDetailPanel
        instrument={instrument()}
        aliasQuery={aliasQuery({
          isError: true,
          error: new ApiError(404, "not found", undefined, "RESOURCE_NOT_FOUND"),
        })}
      />,
    );

    expect(screen.getByText("별칭 정보를 찾을 수 없습니다")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });

  it("negative: 별칭 조회가 RESOURCE_NOT_FOUND가 아닌 에러면 재시도 배너를 보여준다", () => {
    render(
      <InstrumentDetailPanel
        instrument={instrument()}
        aliasQuery={aliasQuery({
          isError: true,
          error: new ApiError(500, "internal error", undefined, "INTERNAL_ERROR"),
        })}
      />,
    );

    expect(
      screen.getByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요."),
    ).toBeInTheDocument();
  });

  it("별칭 목록이 있으면 별칭과 생애주기 전이 이력을 함께 보여준다", () => {
    render(
      <InstrumentDetailPanel
        instrument={instrument()}
        aliasQuery={aliasQuery({
          data: [
            {
              kind: "ok",
              value: {
                alias_id: "a-1",
                instrument_id: "i-1",
                venue: "BITGET",
                alias_symbol: "BTCUSDT",
                valid_from: "2026-01-01T00:00:00Z",
                valid_to: null,
              },
            },
          ],
        })}
      />,
    );

    expect(screen.getByTestId("alias-list")).toHaveTextContent("BTCUSDT");
    expect(screen.getByTestId("lifecycle-timeline")).toHaveTextContent("상장(LIST)");
  });
});
