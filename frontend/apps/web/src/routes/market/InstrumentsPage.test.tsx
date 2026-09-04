import "@testing-library/jest-dom/vitest";
import { ApiError } from "@aios/api-client";
import type { InstrumentView, ParsedInstrumentView, ParsedSymbolAlias, SymbolAlias } from "@aios/shared-types";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InstrumentsPage, type InstrumentsPageProps } from "./InstrumentsPage";

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useAuthStore: { getState: () => ({ token: "token-1" }) },
}));

afterEach(cleanup);

type Client = NonNullable<InstrumentsPageProps["marketDataClient"]>;

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

function ok(overrides: Partial<InstrumentView> = {}): ParsedInstrumentView {
  return { kind: "ok", value: instrument(overrides) };
}

function alias(overrides: Partial<SymbolAlias> = {}): SymbolAlias {
  return {
    alias_id: "a-1",
    instrument_id: "i-1",
    venue: "BITGET",
    alias_symbol: "BTCUSDT",
    valid_from: "2026-01-01T00:00:00Z",
    valid_to: null,
    ...overrides,
  };
}

function okAlias(overrides: Partial<SymbolAlias> = {}): ParsedSymbolAlias {
  return { kind: "ok", value: alias(overrides) };
}

function makeClient(overrides: Partial<Client> = {}): Client {
  return {
    listInstruments: vi.fn(async () => ({ items: [ok()], nextCursor: null })),
    listInstrumentAliases: vi.fn(async () => [okAlias()]),
    ...overrides,
  };
}

function renderPage(client: Client, now?: Date) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <InstrumentsPage marketDataClient={client} now={now} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// task-708(parseInstrumentView/InstrumentLifecycleBadge)·task-462(useCursorPage)를
// 재사용해 목록·상세·필터·페이지네이션을 배선했는지, 파싱 실패나 없는 데이터를
// 조용히 숨기지 않는지를 고정한다.
describe("InstrumentsPage", () => {
  it("venue/status 필터를 쿼리 파라미터로 넘기고 커서로 다음 페이지를 이동한다", async () => {
    const listInstruments = vi.fn(async (params: { venue?: string; status?: string; cursor?: string } = {}) => {
      if (params.cursor === undefined) {
        return { items: [ok({ instrument_id: "i-1", canonical_symbol: "BTC/USDT" })], nextCursor: "cur-2" };
      }
      expect(params.cursor).toBe("cur-2");
      return {
        items: [ok({ instrument_id: "i-2", canonical_symbol: "ETH/USDT", venue_symbol: "ETHUSDT" })],
        nextCursor: null,
      };
    });
    const client = makeClient({ listInstruments });
    renderPage(client);

    await waitFor(() => expect(screen.getByText(/BTC\/USDT/)).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("venue 필터"), { target: { value: "KIS_KRX" } });
    await waitFor(() =>
      expect(listInstruments).toHaveBeenLastCalledWith(expect.objectContaining({ venue: "KIS_KRX", cursor: undefined })),
    );
    await waitFor(() => expect(screen.getByText(/BTC\/USDT/)).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "다음" }));
    await waitFor(() => expect(screen.getByText(/ETH\/USDT/)).toBeInTheDocument());
    expect(screen.queryByText(/BTC\/USDT/)).not.toBeInTheDocument();
  });

  it("행 클릭 시 별칭과 생애주기 전이 이력을 상세 패널에 보여준다", async () => {
    const listInstruments = vi.fn(async () => ({
      items: [ok({ instrument_id: "i-1", listed_at: "2026-01-01T00:00:00Z" })],
      nextCursor: null,
    }));
    const listInstrumentAliases = vi.fn(async () => [
      okAlias({ alias_id: "a-1", alias_symbol: "BTCUSDT", valid_from: "2026-01-01T00:00:00Z", valid_to: "2026-03-01T00:00:00Z" }),
      okAlias({ alias_id: "a-2", alias_symbol: "XBTUSDT", valid_from: "2026-03-01T00:00:00Z", valid_to: null }),
    ]);
    const client = makeClient({ listInstruments, listInstrumentAliases });
    renderPage(client, new Date("2026-09-03T00:00:00Z"));

    await waitFor(() => expect(screen.getByTestId("instrument-row-i-1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("instrument-row-i-1"));

    await waitFor(() => expect(screen.getByTestId("alias-list")).toBeInTheDocument());
    expect(listInstrumentAliases).toHaveBeenCalledWith("i-1");
    expect(screen.getByTestId("alias-list")).toHaveTextContent("XBTUSDT");
    const timeline = screen.getByTestId("lifecycle-timeline");
    expect(timeline).toHaveTextContent("상장(LIST)");
    expect(timeline).toHaveTextContent("별칭 변경(RENAME) → XBTUSDT");
  });

  // task-1088: 행의 "캔들 보기" 링크가 CandlesPage로 instrument_id를 쿼리스트링으로
  // 인계하는지, 기존 행 클릭(선택→상세 패널) 동작과 분리되어 있는지 고정한다.
  it("행의 '캔들 보기' 링크가 instrument_id를 쿼리스트링으로 CandlesPage에 인계한다", async () => {
    const client = makeClient({
      listInstruments: vi.fn(async () => ({ items: [ok({ instrument_id: "i-1" })], nextCursor: null })),
    });
    renderPage(client);

    const link = await screen.findByTestId("instrument-candles-link-i-1");
    expect(link).toHaveAttribute("href", "/market/candles?instrument_id=i-1");
    expect(link).toHaveTextContent("캔들 보기");

    fireEvent.click(link);
    expect(screen.queryByTestId("alias-list")).not.toBeInTheDocument();
  });

  it("negative: 빈 목록이면 EmptyState를 보여준다", async () => {
    const client = makeClient({ listInstruments: vi.fn(async () => ({ items: [], nextCursor: null })) });
    renderPage(client);

    await waitFor(() => expect(screen.getByText("표시할 심볼이 없습니다.")).toBeInTheDocument());
  });

  it("negative: DELISTED 상태를 상장폐지 배지로 그대로 표기한다", async () => {
    const client = makeClient({
      listInstruments: vi.fn(async () => ({
        items: [ok({ instrument_id: "i-3", status: "DELISTED", delisted_at: "2026-05-01T00:00:00Z" })],
        nextCursor: null,
      })),
    });
    renderPage(client);

    await waitFor(() => expect(screen.getByTestId("status-badge")).toHaveTextContent("상장폐지"));
  });

  it("negative: 별칭 조회가 RESOURCE_NOT_FOUND(404)면 재시도 배너 대신 NotFoundState를 보여준다", async () => {
    const listInstruments = vi.fn(async () => ({ items: [ok({ instrument_id: "i-1" })], nextCursor: null }));
    const listInstrumentAliases = vi.fn(async () => {
      throw new ApiError(404, "찾을 수 없습니다", undefined, "RESOURCE_NOT_FOUND");
    });
    const client = makeClient({ listInstruments, listInstrumentAliases });
    renderPage(client);

    await waitFor(() => expect(screen.getByTestId("instrument-row-i-1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("instrument-row-i-1"));

    await waitFor(() => expect(screen.getByText("별칭 정보를 찾을 수 없습니다")).toBeInTheDocument());
    expect(screen.queryByText("요청한 항목을 찾을 수 없습니다.")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });

  it("negative: 별칭 조회가 RESOURCE_NOT_FOUND가 아닌 에러면 재시도 배너를 그대로 보여준다", async () => {
    const listInstruments = vi.fn(async () => ({ items: [ok({ instrument_id: "i-1" })], nextCursor: null }));
    const listInstrumentAliases = vi.fn(async () => {
      throw new ApiError(500, "internal error", undefined, "INTERNAL_ERROR");
    });
    const client = makeClient({ listInstruments, listInstrumentAliases });
    renderPage(client);

    await waitFor(() => expect(screen.getByTestId("instrument-row-i-1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("instrument-row-i-1"));

    await waitFor(() =>
      expect(screen.getByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요.")).toBeInTheDocument(),
    );
  });
});
