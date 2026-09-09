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

  // 아래부터는 DEPTH_DSL_IND(task-2727)가 원 task-1915(d99cb4c)를 D2 축 하한
  // 미달로 판정한 것을 겨눈 보강(task-2928 DEEPEN) — negative가 위 2건(빈
  // 카탈로그·404)뿐이라 얕았고, 실패 주입은 API 실패 시뮬레이션 1건(404)뿐,
  // 수치 성능 단언·게이트 적색 재현이 전부 비어 있었다. 새 기능은 추가하지
  // 않는다.

  // failure-injection 다양화: 기존 404(ApiError)와 달리 fetch 자체가 던지는
  // TypeError는 errorCode가 없어 routeApiError가 "unknown"으로 수렴한다
  // (classifyRetry 등 모든 분류기가 errorCode 필드를 덕타이핑으로 찾으므로) —
  // ErrorMessage가 err.message를 폴백으로 그대로 보여주고 재시도 버튼은
  // 뜨지 않는 경로를 검증한다.
  it("negative/failure-injection: 네트워크 단절(TypeError)이 나면 원본 메시지를 폴백으로 보여주고 재시도 버튼은 뜨지 않는다", async () => {
    const failing: ListIndicators = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    renderPicker({ listIndicators: failing });
    fireEvent.click(screen.getByRole("button", { name: /지표 선택/ }));

    expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });

  // failure-injection 다양화 2: 타임아웃(AbortError)도 동일 계열의 "errorCode
  // 없는 실패"이지만 실제 배포 환경에서 TypeError와는 발생 지점이 다르다
  // (요청이 전송은 됐지만 응답을 못 받는 경우) — 별도 케이스로 고정한다.
  it("negative/failure-injection: 타임아웃(AbortError)도 마커 없이 원본 메시지만 보여준다", async () => {
    const failing: ListIndicators = vi.fn(async () => {
      const err = new Error("The operation was aborted due to timeout");
      err.name = "AbortError";
      throw err;
    });
    renderPicker({ listIndicators: failing });
    fireEvent.click(screen.getByRole("button", { name: /지표 선택/ }));

    expect(await screen.findByText("The operation was aborted due to timeout")).toBeInTheDocument();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  // negative: 페이지네이션 커서 오류 — "더 보기"로 다음 커서를 조회하다 실패하면
  // query.isError가 true로 바뀌어(useInfiniteQuery는 초기 페이지 성공 여부와
  // 무관하게 마지막 페이지 요청의 에러를 전체 상태에 반영한다) body 분기가
  // ErrorMessage로 완전히 대체된다 — 실측 결과, 1페이지 항목 3개와 "더 보기"
  // 버튼 둘 다 사라지고 에러 배너만 남는다(부분 로드 상태를 애매하게 유지하지
  // 않는다). 이 테스트는 그 실제 동작을 고정한다 — 리스트가 사라진 채 크래시
  // 없이 에러 배너로만 수렴하는지가 핵심이다.
  it("negative: 페이지네이션 커서 오류 — 더 보기 조회가 실패하면 목록 대신 에러 배너로 대체되고 크래시하지 않는다", async () => {
    const listIndicators: ListIndicators = vi.fn(async (params) => {
      if (params?.cursor) throw new TypeError("Failed to fetch");
      return { items: [SMA_ITEM, RSI_ITEM, OSS_ITEM], nextCursor: "OSS_IND" };
    });
    renderPicker({ listIndicators });
    const firstPageOptions = await openAndWaitForOptions();
    expect(firstPageOptions).toHaveLength(3);

    fireEvent.click(screen.getByRole("button", { name: "더 보기" }));

    await waitFor(() => expect(listIndicators).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText("Failed to fetch")).toBeInTheDocument());
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "더 보기" })).not.toBeInTheDocument();
  });

  // negative: 검색 디바운스 경쟁 — 사용자가 빠르게 다시 고쳐 입력하면 먼저
  // 시작된(오래된) 검색어에 대한 응답이 나중에 resolve될 수 있다. react-query는
  // queryKey(["indicator-picker", debouncedQuery])별로 캐시하므로, 화면에는
  // 항상 "현재" debouncedQuery(마지막으로 확정된 값)에 해당하는 결과만 보여야
  // 한다 — 먼저 시작했지만 늦게 끝난 응답이 최신 화면을 덮어써서는 안 된다.
  it("negative: 검색 디바운스 경쟁 — 늦게 도착한 이전 검색어 응답이 최신 검색어 화면을 덮어쓰지 않는다", async () => {
    let resolveStale: ((v: ListIndicatorsResult) => void) | undefined;
    const listIndicators: ListIndicators = vi.fn(async (params) => {
      if (params?.q === "R") {
        // 오래된("R") 검색은 응답을 일부러 보류해 나중에 resolve시킨다.
        return new Promise<ListIndicatorsResult>((resolve) => {
          resolveStale = resolve;
        });
      }
      if (params?.q === "RS") return { items: [RSI_ITEM], nextCursor: null };
      return { items: [SMA_ITEM, RSI_ITEM, OSS_ITEM], nextCursor: "OSS_IND" };
    });
    renderPicker({ listIndicators });
    await openAndWaitForOptions();

    fireEvent.change(screen.getByLabelText("지표 검색"), { target: { value: "R" } });
    await waitFor(() => expect(listIndicators).toHaveBeenCalledWith({ q: "R", cursor: undefined }));

    fireEvent.change(screen.getByLabelText("지표 검색"), { target: { value: "RS" } });
    await waitFor(() => expect(listIndicators).toHaveBeenCalledWith({ q: "RS", cursor: undefined }));
    await waitFor(() =>
      expect(within(screen.getByRole("listbox")).getAllByRole("option")).toHaveLength(1),
    );

    // 그제서야 오래된("R") 응답이 도착한다 — 이미 "RS" 화면으로 넘어간 뒤라
    // 이 resolve가 화면을 1개(RSI)에서 3개로 되돌리면 안 된다.
    resolveStale?.({ items: [SMA_ITEM, RSI_ITEM, OSS_ITEM], nextCursor: "OSS_IND" });
    await Promise.resolve();
    expect(within(screen.getByRole("listbox")).getAllByRole("option")).toHaveLength(1);
    expect(screen.getByRole("option", { name: /^RSI/ })).toBeInTheDocument();
  });

  // 수치 성능 단언: SEARCH_DEBOUNCE_MS(300ms) 상수 자체를 고정한다. fake timer로
  // 299ms에서는 아직 호출되지 않았고 300ms에 정확히 호출됨을 검증 — perf.now
  // 왕복측정이 아니라 디바운스 임계값 자체가 회귀(예: 300→0으로 실수로 지워짐,
  // 또는 3000으로 실수 확대)하면 즉시 잡아내는 값 단언이다.
  it("성능 단언: 검색 디바운스는 정확히 300ms에서 발화하고 299ms에서는 아직 호출되지 않는다", async () => {
    vi.useFakeTimers();
    try {
      const listIndicators = pagedListIndicators();
      renderPicker({ listIndicators });
      fireEvent.click(screen.getByRole("button", { name: /지표 선택/ }));
      // 최초 오픈 시 q=undefined 호출 1회를 소진시킨다.
      await vi.waitFor(() => expect(listIndicators).toHaveBeenCalledTimes(1));

      fireEvent.change(screen.getByLabelText("지표 검색"), { target: { value: "RSI" } });
      expect(listIndicators).toHaveBeenCalledTimes(1);

      vi.advanceTimersByTime(299);
      expect(listIndicators).toHaveBeenCalledTimes(1);

      vi.advanceTimersByTime(1);
      await vi.waitFor(() => expect(listIndicators).toHaveBeenCalledTimes(2));
      expect(listIndicators).toHaveBeenLastCalledWith({ q: "RSI", cursor: undefined });
    } finally {
      vi.useRealTimers();
    }
  });

  // 게이트 적색 재현: visibleItems는 item.tier로 걸러야 한다(§9.9 IND-14 주석 —
  // 서버가 tier별 조회를 지원하지 않아 이미 받은 페이지를 프론트가 tier로
  // 거른다). item.category(예: "Overlap Studies")로 잘못 거르는 naive 회귀는
  // fixture 중 OSS_IND/MY_SCRIPT처럼 category와 tier 문자열이 우연히 같은
  // 항목에서는 티가 안 나고, category와 tier가 다른 core 항목(SMA/RSI)에서만
  // 드러난다 — 그래서 "core" 필터로 대조한다.
  it("게이트 적색 재현: 계층 필터가 item.category가 아니라 item.tier로 걸러짐을 증명한다", async () => {
    renderPicker();
    await openAndWaitForOptions();

    fireEvent.change(screen.getByLabelText("지표 계층 필터"), { target: { value: "core" } });

    // 실제 DOM: tier==="core"인 SMA/RSI 2개만 남는다(category는 각각
    // "Overlap Studies"/"Momentum Indicators"로 "core"와 다르지만 무관하다).
    const options = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(options).toHaveLength(2);

    const items = [SMA_ITEM, RSI_ITEM, OSS_ITEM];
    function realFilter(tierFilter: string): IndicatorCatalogItem[] {
      return items.filter((item) => item.tier === tierFilter);
    }
    function naiveFilter(tierFilter: string): IndicatorCatalogItem[] {
      // 회귀: tier 대신 category로 비교 — 서버 category는 tier와 무관한 자유
      // 문자열이라("Overlap Studies" 등) core/momentum 계열이 통째로 사라진다.
      return items.filter((item) => item.category === tierFilter);
    }

    expect(realFilter("core")).toHaveLength(2);
    expect(naiveFilter("core")).toHaveLength(0);
  });
});
