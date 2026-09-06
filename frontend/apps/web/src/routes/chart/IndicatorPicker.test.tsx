import "@testing-library/jest-dom/vitest";
import { ApiError, type IndicatorCatalogItem, type ListIndicatorsResult } from "@aios/api-client";
import { createDefaultOverlayRegistry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IndicatorPicker, type ListIndicators } from "./IndicatorPicker";

// ChartPage.test.tsx/CompareSymbols.test.tsx와 동일 관용: ErrorMessage는 err
// instanceof ApiError로 errorCode를 뽑으므로(err.message 직접 렌더 금지) 실제
// ApiError 인스턴스로만 검증한다.
function apiErrorLike(statusCode: number, errorCode: string): ApiError {
  return new ApiError(statusCode, errorCode, undefined, errorCode);
}

afterEach(cleanup);

const AVAILABLE = createDefaultOverlayRegistry().list();

function catalogItem(overrides: Partial<IndicatorCatalogItem>): IndicatorCatalogItem {
  return {
    name: "SMA",
    tier: "core",
    category: "Overlap Studies",
    version: "1",
    hash: "h",
    inputs: ["close"],
    outputs: ["value"],
    ...overrides,
  };
}

const SMA_ITEM = catalogItem({ name: "SMA", tier: "core", category: "Overlap Studies" });
const RSI_ITEM = catalogItem({ name: "RSI", tier: "core", category: "Momentum Indicators" });
const OSS_ITEM = catalogItem({ name: "OSS_IND", tier: "oss", category: "oss" });
const SCRIPT_ITEM = catalogItem({ name: "MY_SCRIPT", tier: "script", category: "script" });

function pagedListIndicators(): ListIndicators {
  return vi.fn(async (params) => {
    if (params?.cursor) return { items: [SCRIPT_ITEM], nextCursor: null };
    return { items: [SMA_ITEM, RSI_ITEM, OSS_ITEM], nextCursor: "OSS_IND" };
  });
}

function renderPicker(options: {
  listIndicators?: ListIndicators;
  selectedIds?: readonly string[];
  onToggle?: (id: string) => void;
} = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onToggle = options.onToggle ?? vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <IndicatorPicker
        available={AVAILABLE}
        selectedIds={options.selectedIds ?? []}
        onToggle={onToggle}
        listIndicators={options.listIndicators ?? pagedListIndicators()}
      />
    </QueryClientProvider>,
  );
  return { onToggle };
}

// <select>의 <option>도 role="option"이라 screen 전역 쿼리는 계층 필터와
// 뒤섞인다 — listbox(<ul role="listbox">) 안으로만 스코프한다.
async function openAndWaitForOptions() {
  fireEvent.click(screen.getByRole("button", { name: /지표 선택/ }));
  const listbox = await screen.findByRole("listbox");
  return within(listbox).getAllByRole("option");
}

describe("IndicatorPicker", () => {
  it("트리거 버튼을 누르면 listIndicators를 호출하고 서버 카탈로그가 listbox로 열린다", async () => {
    const listIndicators = pagedListIndicators();
    renderPicker({ listIndicators });

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    const options = await openAndWaitForOptions();

    expect(listIndicators).toHaveBeenCalledWith({ q: undefined, cursor: undefined });
    expect(options).toHaveLength(3);
    expect(screen.getByRole("option", { name: /^SMA/ })).toHaveTextContent("코어 · 메인");
    expect(screen.getByRole("option", { name: /^OSS_IND/ })).toHaveTextContent("OSS · oss");
  });

  it("옵션을 클릭하면 onToggle이 해당 name으로 호출된다", async () => {
    const { onToggle } = renderPicker();
    await openAndWaitForOptions();

    fireEvent.click(screen.getByRole("option", { name: /^SMA/ }));
    expect(onToggle).toHaveBeenCalledWith("SMA");
  });

  it("선택된 지표는 aria-selected=true이고 선택 칩으로 표시된다", async () => {
    renderPicker({ selectedIds: ["SMA"] });
    await openAndWaitForOptions();

    expect(screen.getByRole("option", { name: /SMA/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "SMA ✕" })).toBeInTheDocument();
  });

  it("ArrowDown/Enter로 키보드만으로 지표를 토글할 수 있다", async () => {
    const { onToggle } = renderPicker();
    await openAndWaitForOptions();

    const listbox = screen.getByRole("listbox");
    fireEvent.keyDown(listbox, { key: "ArrowDown" });
    fireEvent.keyDown(listbox, { key: "Enter" });
    expect(onToggle).toHaveBeenCalledWith("RSI");
  });

  it("Escape를 누르면 목록이 닫히고 트리거로 포커스가 돌아간다", async () => {
    renderPicker();
    const trigger = screen.getByRole("button", { name: /지표 선택/ });
    fireEvent.click(trigger);
    await screen.findByRole("listbox");

    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(trigger);
  });

  it("계층 필터를 OSS로 바꾸면 코어/스크립트 항목은 숨고 OSS 항목만 남는다", async () => {
    renderPicker();
    await openAndWaitForOptions();

    fireEvent.change(screen.getByLabelText("지표 계층 필터"), { target: { value: "oss" } });

    const options = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent("OSS_IND");
  });

  it("검색어 입력이 디바운스 후 q 파라미터로 서버에 전달된다", async () => {
    const listIndicators = pagedListIndicators();
    renderPicker({ listIndicators });
    await openAndWaitForOptions();

    fireEvent.change(screen.getByLabelText("지표 검색"), { target: { value: "  RS  " } });

    await waitFor(() => expect(listIndicators).toHaveBeenCalledWith({ q: "RS", cursor: undefined }));
  });

  it("커서 페이지네이션: 더 보기를 누르면 다음 페이지 항목이 이어붙는다", async () => {
    renderPicker();
    await openAndWaitForOptions();

    const more = screen.getByRole("button", { name: "더 보기" });
    fireEvent.click(more);

    await waitFor(() => expect(within(screen.getByRole("listbox")).getAllByRole("option")).toHaveLength(4));
    expect(screen.getByRole("option", { name: /^MY_SCRIPT/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "더 보기" })).not.toBeInTheDocument();
  });

  it("negative: 카탈로그가 비어 있으면 안내 문구만 보여준다", async () => {
    const empty: ListIndicators = vi.fn(async (): Promise<ListIndicatorsResult> => ({ items: [], nextCursor: null }));
    renderPicker({ listIndicators: empty });
    fireEvent.click(screen.getByRole("button", { name: /지표 선택/ }));

    expect(await screen.findByText("지표가 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("negative: 카탈로그 조회가 실패하면 ErrorMessage로만 노출한다(err.message 직접 렌더 금지)", async () => {
    const failing: ListIndicators = vi.fn(async () => {
      throw apiErrorLike(404, "RESOURCE_NOT_FOUND");
    });
    renderPicker({ listIndicators: failing });
    fireEvent.click(screen.getByRole("button", { name: /지표 선택/ }));

    expect(await screen.findByText("요청한 항목을 찾을 수 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
});
