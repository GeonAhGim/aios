import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { AlertFromChart, type AlertFromChartProps } from "./AlertFromChart";

const createAlertMutateAsync = vi.fn();

vi.mock("@aios/shared-hooks", () => ({
  useCreateAlert: () => ({ mutateAsync: createAlertMutateAsync, isPending: false }),
}));

afterEach(() => {
  cleanup();
  createAlertMutateAsync.mockReset();
});

function baseProps(): AlertFromChartProps {
  return {
    isOpen: true,
    onClose: vi.fn(),
    venue: "BITGET",
    instrumentId: "BTCUSDT",
    timeframe: "1h",
    currentClose: 50200,
    selectedIndicatorIds: [],
  };
}

function renderDialog(overrides: Partial<AlertFromChartProps> = {}) {
  return render(
    <MemoryRouter>
      <AlertFromChart {...baseProps()} {...overrides} />
    </MemoryRouter>,
  );
}

describe("AlertFromChart", () => {
  it("닫혀 있으면 아무것도 렌더링하지 않는다", () => {
    const { container } = renderDialog({ isOpen: false });
    expect(container).toBeEmptyDOMElement();
  });

  it("차트 컨텍스트(심볼·현재가)로 다이얼로그와 임계값 입력을 프리필한다", () => {
    renderDialog({ instrumentId: "ETHUSDT", currentClose: 3123.45 });

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveTextContent("ETHUSDT");
    expect(dialog).toHaveTextContent("3123.45");
    expect(screen.getByLabelText("임계값")).toHaveValue(3123.45);
    expect(screen.getByLabelText("방향")).toHaveValue("above");
  });

  it("지표 교차 조건은 비활성화되어 있고 선택된 지표를 사유에 표시한다(needs_decision 아님, STALE)", () => {
    renderDialog({ selectedIndicatorIds: ["RSI", "SMA"] });

    const indicatorRadio = screen.getByRole("radio", { name: /지표 교차/ });
    expect(indicatorRadio).toBeDisabled();
    expect(screen.getByText(/RSI, SMA/)).toBeInTheDocument();
  });

  it("제출 시 서버 계약 필드명 그대로(AlertCreateRequest 1:1) payload를 보낸다", async () => {
    createAlertMutateAsync.mockResolvedValue({ id: 42 });
    renderDialog({ venue: "KIS_KRX", instrumentId: "005930", currentClose: 70000 });

    fireEvent.click(screen.getByRole("button", { name: "알림 등록" }));

    await waitFor(() =>
      expect(createAlertMutateAsync).toHaveBeenCalledWith({
        exchange: "kis",
        symbol: "005930",
        timeframe: "1h",
        indicator: "SMA",
        params: { timeperiod: 1 },
        operator: ">",
        threshold: 70000,
      }),
    );
  });

  it("below 방향을 고르면 연산자가 '<'로 바뀐다", async () => {
    createAlertMutateAsync.mockResolvedValue({ id: 1 });
    renderDialog();

    fireEvent.change(screen.getByLabelText("방향"), { target: { value: "below" } });
    fireEvent.click(screen.getByRole("button", { name: "알림 등록" }));

    await waitFor(() =>
      expect(createAlertMutateAsync).toHaveBeenCalledWith(expect.objectContaining({ operator: "<" })),
    );
  });

  it("성공하면 AlertsPage로 가는 링크를 보여준다", async () => {
    createAlertMutateAsync.mockResolvedValue({ id: 99 });
    renderDialog();

    fireEvent.click(screen.getByRole("button", { name: "알림 등록" }));

    const link = await screen.findByRole("link", { name: /알림 목록에서 확인/ });
    expect(link).toHaveAttribute("href", "/alerts");
  });

  it("negative: 400 VALIDATION_INVALID_FIELD는 서버 message를 배너로 보여준다", async () => {
    createAlertMutateAsync.mockRejectedValue(
      new ApiError(400, "임계값을 확인해주세요.", "trace-1", "VALIDATION_INVALID_FIELD", undefined, {
        fields: [],
      }),
    );
    renderDialog();

    fireEvent.click(screen.getByRole("button", { name: "알림 등록" }));

    await waitFor(() => expect(screen.getByText("임계값을 확인해주세요.")).toBeInTheDocument());
  });

  it("negative: POLICY_*(403) 거부는 ForbiddenNotice 매핑 문구를 보여주고 원문을 노출하지 않는다", async () => {
    createAlertMutateAsync.mockRejectedValue(
      new ApiError(403, "raw server detail", "trace-2", "POLICY_LIVE_BLOCKED"),
    );
    renderDialog();

    fireEvent.click(screen.getByRole("button", { name: "알림 등록" }));

    await waitFor(() =>
      expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: 429 RATE_LIMIT_EXCEEDED는 raw message 없이 taxonomy 안내 문구를 보여준다", async () => {
    createAlertMutateAsync.mockRejectedValue(
      new ApiError(429, "raw rate limit detail", "trace-3", "RATE_LIMIT_EXCEEDED", 12),
    );
    renderDialog();

    fireEvent.click(screen.getByRole("button", { name: "알림 등록" }));

    await waitFor(() =>
      expect(screen.getByText("요청이 너무 많습니다. 잠시 후 다시 시도해주세요.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw rate limit detail")).not.toBeInTheDocument();
  });

  it("Esc를 누르면 onClose가 호출된다", () => {
    const onClose = vi.fn();
    renderDialog({ onClose });

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Tab 포커스는 다이얼로그 안에서만 순환한다(포커스 트랩)", () => {
    renderDialog();

    const dialog = screen.getByRole("dialog");
    const focusable = dialog.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), [href]',
    );
    const first = focusable[0]!;
    const last = focusable[focusable.length - 1]!;

    last.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(first);

    first.focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
  });
});

// DEEPEN(task-3079) of task-1570 (CH-9, commit 9554f6b), per DEPTH_CH audit
// (task-2729, docs/audit/DEPTH_CH.md): the original 12-test leaf (+ 3 wiring
// tests in ChartToolbar.test.tsx) already had negative>=3 and mocked
// ApiError failure injection, but zero numeric performance assertion, no
// gate-red reproduction, and no D3 (adversarial/multi-instance) proof. This
// block fills those gaps.
describe("DEEPEN(task-3079): numeric perf, gate-red, D3", () => {
  it("수치 성능: 임계값 입력에 200회 연속 keystroke를 흘려도 6000ms 예산 내에 처리된다", () => {
    renderDialog();
    const input = screen.getByLabelText("임계값");

    const startedAt = performance.now();
    let value = "";
    for (let i = 0; i < 200; i += 1) {
      value += (i % 10).toString();
      fireEvent.change(input, { target: { value } });
    }
    const elapsedMs = performance.now() - startedAt;

    expect(input).toHaveValue(Number(value));
    expect(elapsedMs).toBeLessThan(6000);
  });

  it("게이트 적색 재현: 열려 있는 동안 currentClose가 바뀌어도 임계값을 덮어쓰지 않는다 — 프리필 effect 의존성에 currentClose를 추가하면 적색", () => {
    const { rerender } = renderDialog({ currentClose: 100 });
    expect(screen.getByLabelText("임계값")).toHaveValue(100);

    fireEvent.change(screen.getByLabelText("임계값"), { target: { value: "150" } });
    expect(screen.getByLabelText("임계값")).toHaveValue(150);

    rerender(
      <MemoryRouter>
        <AlertFromChart {...baseProps()} currentClose={999} />
      </MemoryRouter>,
    );

    expect(screen.getByLabelText("임계값")).toHaveValue(150);
  });

  it("게이트 적색 재현: 닫았다가 다시 열면(isOpen false→true 전이) 새 currentClose로 다시 프리필된다 — 전이 감지가 깨지면(예: 빈 deps) 적색", () => {
    const { rerender } = renderDialog({ currentClose: 100 });
    fireEvent.change(screen.getByLabelText("임계값"), { target: { value: "150" } });
    expect(screen.getByLabelText("임계값")).toHaveValue(150);

    rerender(
      <MemoryRouter>
        <AlertFromChart {...baseProps()} isOpen={false} currentClose={100} />
      </MemoryRouter>,
    );
    rerender(
      <MemoryRouter>
        <AlertFromChart {...baseProps()} isOpen currentClose={777} />
      </MemoryRouter>,
    );

    expect(screen.getByLabelText("임계값")).toHaveValue(777);
  });

  it("어드버서리얼(D3): 서로 다른 차트 패널에 동시에 뜬 두 인스턴스가 임계값 상태를 공유하지 않는다", () => {
    render(
      <MemoryRouter>
        <div>
          <AlertFromChart {...baseProps()} instrumentId="BTCUSDT" currentClose={100} />
          <AlertFromChart {...baseProps()} instrumentId="ETHUSDT" currentClose={200} />
        </div>
      </MemoryRouter>,
    );

    const dialogs = screen.getAllByRole("dialog");
    expect(dialogs).toHaveLength(2);
    // AlertFromChart는 정적 id="alert-threshold"를 쓴다(단일 다이얼로그 전제) — 두
    // 인스턴스가 동시에 열리면 문서 전체에서 id가 중복되어 getByLabelText는 신뢰할
    // 수 없으므로, 각 다이얼로그 서브트리 안에서 직접 DOM을 조회해 React 상태
    // 자체의 인스턴스별 독립성만 검증한다.
    const threshold0 = dialogs[0]!.querySelector<HTMLInputElement>("#alert-threshold")!;
    const threshold1 = dialogs[1]!.querySelector<HTMLInputElement>("#alert-threshold")!;
    expect(threshold0.value).toBe("100");
    expect(threshold1.value).toBe("200");

    fireEvent.change(threshold0, { target: { value: "555" } });

    expect(threshold0.value).toBe("555");
    expect(threshold1.value).toBe("200");
  });

  it("어드버서리얼(D3): 응답 대기 중 연속 두 번 클릭하면 mutateAsync가 두 번 호출된다 — 컴포넌트 자체엔 재요청 가드가 없고 disabled는 TanStack Query의 isPending 배선에만 의존한다(mock의 isPending은 항상 false)", async () => {
    let resolvePromise!: (value: { id: number }) => void;
    createAlertMutateAsync.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvePromise = resolve;
        }),
    );
    renderDialog();

    const submit = screen.getByRole("button", { name: "알림 등록" });
    fireEvent.click(submit);
    fireEvent.click(submit);

    expect(createAlertMutateAsync).toHaveBeenCalledTimes(2);

    resolvePromise({ id: 1 });
    await waitFor(() => expect(screen.getByText(/알림이 등록되었습니다/)).toBeInTheDocument());
  });
});
