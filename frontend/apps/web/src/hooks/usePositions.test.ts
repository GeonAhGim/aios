import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  positionsQueryKeys,
  useNavSeries,
  usePositionJournal,
  usePositionList,
  usePositionsClient,
} from "./usePositions";

const createPositionsClientMock = vi.fn((_baseUrl: string, _getToken: () => string | null) => ({
  listPositions: vi.fn(),
  getPositionJournal: vi.fn(),
  getNavSeries: vi.fn(),
}));
vi.mock("@aios/api-client", () => ({
  createPositionsClient: (baseUrl: string, getToken: () => string | null) => createPositionsClientMock(baseUrl, getToken),
}));

const authGetState = vi.fn(() => ({ token: "tok-1" }));
vi.mock("@aios/shared-hooks", () => ({
  useAuthStore: { getState: () => authGetState() },
}));

afterEach(() => {
  vi.clearAllMocks();
});

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return createElement(QueryClientProvider, { client: qc }, children);
}

describe("positionsQueryKeys", () => {
  it("list: accountId/instrumentId가 없으면 null로 채운다", () => {
    expect(positionsQueryKeys.list({})).toEqual(["positions", "list", null, null]);
  });

  it("list: accountId/instrumentId가 있으면 키에 포함한다", () => {
    expect(positionsQueryKeys.list({ accountId: "acc-1", instrumentId: "BTC-KRW" })).toEqual([
      "positions",
      "list",
      "acc-1",
      "BTC-KRW",
    ]);
  });

  it("journal: cursor는 불투명 문자열을 그대로 넣는다(숫자 변환 없음)", () => {
    expect(positionsQueryKeys.journal("pos-1", "0002", 20)).toEqual(["positions", "journal", "pos-1", "0002", 20]);
  });

  it("journal: cursor/limit이 없으면 null로 채운다", () => {
    expect(positionsQueryKeys.journal("pos-1", undefined, undefined)).toEqual([
      "positions",
      "journal",
      "pos-1",
      null,
      null,
    ]);
  });

  it("nav: accountId/startDate/endDate로 키를 구성한다", () => {
    expect(positionsQueryKeys.nav({ accountId: "acc-1", startDate: "2026-01-01", endDate: "2026-01-31" })).toEqual([
      "positions",
      "nav",
      "acc-1",
      "2026-01-01",
      "2026-01-31",
    ]);
  });
});

describe("usePositionsClient", () => {
  it("client를 주입하면 그 client를 그대로 반환한다(createPositionsClient 호출 안 함)", () => {
    const injected = { listPositions: vi.fn(), getPositionJournal: vi.fn(), getNavSeries: vi.fn() };
    const { result } = renderHook(() => usePositionsClient(injected));

    expect(result.current).toBe(injected);
    expect(createPositionsClientMock).not.toHaveBeenCalled();
  });

  it("주입이 없으면 createPositionsClient로 현재 토큰을 읽는 팩토리를 넘겨 client를 만든다", () => {
    renderHook(() => usePositionsClient());

    expect(createPositionsClientMock).toHaveBeenCalledTimes(1);
    const [, getToken] = createPositionsClientMock.mock.calls[0]!;
    expect(getToken()).toBe("tok-1");
  });
});

describe("usePositionList", () => {
  it("client.listPositions에 params를 그대로 넘겨 결과를 반환한다", async () => {
    const listPositions = vi.fn().mockResolvedValue({ items: [], nextCursor: null });
    const { result } = renderHook(() => usePositionList({ listPositions }, { accountId: "acc-1" }), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(listPositions).toHaveBeenCalledWith({ accountId: "acc-1" });
  });

  it("negative: enabled=false면 요청하지 않는다", () => {
    const listPositions = vi.fn().mockResolvedValue({ items: [], nextCursor: null });
    renderHook(() => usePositionList({ listPositions }, {}, { enabled: false }), { wrapper });

    expect(listPositions).not.toHaveBeenCalled();
  });
});

describe("usePositionJournal", () => {
  it("positionKey/cursor/limit을 client.getPositionJournal에 넘긴다", async () => {
    const getPositionJournal = vi.fn().mockResolvedValue({ positionKey: "pos-1", items: [], nextCursor: null });
    const { result } = renderHook(
      () => usePositionJournal({ getPositionJournal }, "pos-1", { cursor: "0002", limit: 10 }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getPositionJournal).toHaveBeenCalledWith({ positionKey: "pos-1", cursor: "0002", limit: 10 });
  });

  it("negative: enabled=false면 요청하지 않는다(저널 패널이 접혀 있을 때)", () => {
    const getPositionJournal = vi.fn();
    renderHook(() => usePositionJournal({ getPositionJournal }, "pos-1", { enabled: false }), { wrapper });

    expect(getPositionJournal).not.toHaveBeenCalled();
  });
});

describe("useNavSeries", () => {
  it("params가 있으면 client.getNavSeries에 그대로 넘긴다", async () => {
    const getNavSeries = vi.fn().mockResolvedValue({ points: [] });
    const params = { accountId: "acc-1", startDate: "2026-01-01", endDate: "2026-01-31" };
    const { result } = renderHook(() => useNavSeries({ getNavSeries }, params), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getNavSeries).toHaveBeenCalledWith(params);
  });

  // negative: account_id는 서버 필수 파라미터라 표시할 계좌가 없을 때 빈 값으로
  // 보내면 VALIDATION 오류만 만든다 — params가 null이면 아예 요청하지 않아야 한다.
  it("negative: params가 null이면 요청하지 않는다", () => {
    const getNavSeries = vi.fn();
    const { result } = renderHook(() => useNavSeries({ getNavSeries }, null), { wrapper });

    expect(getNavSeries).not.toHaveBeenCalled();
    expect(result.current.fetchStatus).toBe("idle");
  });
});
