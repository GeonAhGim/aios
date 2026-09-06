import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ComponentProps } from "react";
import type { ChartIndicatorTemplateRecord } from "@aios/api-client";
import { ChartTemplates, type ChartTemplatesPort, type TemplateApplyResult } from "./ChartTemplates";

function fakePort(overrides: Partial<ChartTemplatesPort> = {}): ChartTemplatesPort {
  return {
    createIndicatorTemplate: vi.fn(),
    listIndicatorTemplates: vi.fn(async () => []),
    deleteIndicatorTemplate: vi.fn(),
    ...overrides,
  };
}

function templateRecord(overrides: Partial<ChartIndicatorTemplateRecord> = {}): ChartIndicatorTemplateRecord {
  return {
    id: "template-1",
    tenantId: "tenant-1",
    ownerSubjectId: "owner-1",
    name: "My template",
    template: {
      schemaVersion: 1,
      panes: [
        { id: "main", kind: "main", heightRatio: 0.7 },
        { id: "sub-RSI", kind: "sub", heightRatio: 0.3 },
      ],
      indicators: [
        { id: "SMA", paneId: "main" },
        { id: "RSI", paneId: "sub-RSI" },
      ],
    },
    revision: 0,
    createdAt: "2026-09-07T00:00:00Z",
    updatedAt: "2026-09-07T00:00:00Z",
    ...overrides,
  };
}

function renderTemplates(props: Partial<Omit<ComponentProps<typeof ChartTemplates>, "onApplied">> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const onApplied = vi.fn();
  const port = props.port ?? fakePort();
  render(
    <QueryClientProvider client={queryClient}>
      <ChartTemplates
        port={port}
        mainIndicatorIds={props.mainIndicatorIds ?? ["SMA"]}
        subIndicatorIds={props.subIndicatorIds ?? ["RSI"]}
        knownIndicatorIds={props.knownIndicatorIds ?? new Set(["SMA", "RSI"])}
        onApplied={onApplied}
      />
    </QueryClientProvider>,
  );
  return { port, onApplied };
}

afterEach(cleanup);

describe("ChartTemplates", () => {
  it("템플릿 버튼을 열면 목록을 조회한다(마운트 시점에는 조회하지 않는다)", async () => {
    const { port } = renderTemplates();
    expect(port.listIndicatorTemplates).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "템플릿" }));
    await waitFor(() => expect(port.listIndicatorTemplates).toHaveBeenCalledTimes(1));
  });

  it("현재 차트 저장: CH-17a capture()로 만든 template JSON을 name과 함께 전송한다", async () => {
    const createIndicatorTemplate = vi.fn().mockResolvedValue(templateRecord());
    const { port } = renderTemplates({ port: fakePort({ createIndicatorTemplate }) });

    fireEvent.click(screen.getByRole("button", { name: "템플릿" }));
    await waitFor(() => expect(port.listIndicatorTemplates).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("템플릿 이름"), { target: { value: "내 템플릿" } });
    fireEvent.click(screen.getByTestId("chart-templates-save"));

    await waitFor(() => expect(createIndicatorTemplate).toHaveBeenCalledTimes(1));
    const input = createIndicatorTemplate.mock.calls[0]?.[0];
    expect(input.name).toBe("내 템플릿");
    expect(input.template).toEqual({
      schemaVersion: 1,
      panes: [
        { id: "main", kind: "main", heightRatio: 0.5 },
        { id: "sub-RSI", kind: "sub", heightRatio: 0.5 },
      ],
      indicators: [
        { id: "SMA", paneId: "main" },
        { id: "RSI", paneId: "sub-RSI" },
      ],
    });
  });

  it("빈 이름으로는 저장하지 않는다", async () => {
    const createIndicatorTemplate = vi.fn();
    renderTemplates({ port: fakePort({ createIndicatorTemplate }) });
    fireEvent.click(screen.getByRole("button", { name: "템플릿" }));
    fireEvent.click(await screen.findByTestId("chart-templates-save"));
    expect(createIndicatorTemplate).not.toHaveBeenCalled();
  });

  it("템플릿 적용: CH-17a apply()가 계산한 indicatorIds·paneHeightRatios로 onApplied를 호출한다", async () => {
    const record = templateRecord();
    const { onApplied } = renderTemplates({
      port: fakePort({ listIndicatorTemplates: vi.fn(async () => [record]) }),
      mainIndicatorIds: [],
      subIndicatorIds: [],
    });

    fireEvent.click(screen.getByRole("button", { name: "템플릿" }));
    fireEvent.click(await screen.findByTestId(`chart-templates-apply-${record.id}`));

    await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1));
    const result: TemplateApplyResult = onApplied.mock.calls[0]?.[0];
    expect(result.indicatorIds).toEqual(["SMA", "RSI"]);
    expect(result.paneHeightRatios).toEqual({ main: 0.7, "sub-RSI": 0.3 });
  });

  it("적용 대상 지표가 knownIndicatorIds에 없으면 fail-closed로 거부하고 에러를 보여준다(onApplied 미호출)", async () => {
    const record = templateRecord();
    const { onApplied } = renderTemplates({
      port: fakePort({ listIndicatorTemplates: vi.fn(async () => [record]) }),
      knownIndicatorIds: new Set(["SMA"]), // RSI 누락
    });

    fireEvent.click(screen.getByRole("button", { name: "템플릿" }));
    fireEvent.click(await screen.findByTestId(`chart-templates-apply-${record.id}`));

    await waitFor(() => expect(screen.getByText(/unknown_indicator/)).toBeInTheDocument());
    expect(onApplied).not.toHaveBeenCalled();
  });

  it("삭제 버튼을 누르면 deleteIndicatorTemplate를 호출한다", async () => {
    const record = templateRecord();
    const deleteIndicatorTemplate = vi.fn().mockResolvedValue(undefined);
    renderTemplates({
      port: fakePort({ listIndicatorTemplates: vi.fn(async () => [record]), deleteIndicatorTemplate }),
    });

    fireEvent.click(screen.getByRole("button", { name: "템플릿" }));
    fireEvent.click(await screen.findByTestId(`chart-templates-delete-${record.id}`));

    await waitFor(() => expect(deleteIndicatorTemplate).toHaveBeenCalledWith(record.id));
  });
});
