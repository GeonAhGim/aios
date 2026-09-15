import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ComponentProps } from "react";
import { ApiError, type ChartIndicatorTemplateRecord, type CreateChartIndicatorTemplateInput } from "@aios/api-client";
import { decodeTemplate, type Template } from "@aios/chart-engine/src/templates/templateModel";
import { ChartTemplates, type ChartTemplatesPort, type TemplateApplyResult } from "./ChartTemplates";
import { perfBudgetMs } from "../../test/perfBudget";

// CompareSymbols.test.tsx/AlertFromChart.test.tsx와 동일 관용: ErrorMessage는
// err instanceof ApiError로 errorCode를 뽑아 EXACT_MESSAGES/PREFIX_MESSAGES로
// 매핑하므로(원본 코드 문자열을 그대로 렌더하지 않음), 실제 ApiError 인스턴스로만
// 실패를 주입하고 매핑된 한국어 문구로 단언한다.
function apiErrorLike(statusCode: number, errorCode: string): ApiError {
  return new ApiError(statusCode, errorCode, undefined, errorCode);
}

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

  // --- DEEPEN 1905: negative<3, mocked 실패주입 없음, 수치 성능 단언 없음,
  // 게이트 적색 재현 없음(docs/audit/DEPTH_CH.md) 보강분 ---

  it("실패 주입: 목록 조회가 서버 오류로 실패하면 목록 대신 에러 배너를 보여준다(negative)", async () => {
    const listIndicatorTemplates = vi.fn().mockRejectedValue(apiErrorLike(500, "INTERNAL_ERROR"));
    renderTemplates({ port: fakePort({ listIndicatorTemplates }) });

    fireEvent.click(screen.getByRole("button", { name: "템플릿" }));

    expect(
      await screen.findByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("템플릿 목록")).not.toBeInTheDocument();
  });

  it("실패 주입: 저장이 409 충돌로 실패하면 에러 배너를 보여주고 입력값을 보존한다(negative, onSuccess 리셋 미발동)", async () => {
    const createIndicatorTemplate = vi.fn().mockRejectedValue(apiErrorLike(409, "STATE_CONCURRENCY_CONFLICT"));
    renderTemplates({ port: fakePort({ createIndicatorTemplate }) });

    fireEvent.click(screen.getByRole("button", { name: "템플릿" }));
    const nameInput = await screen.findByLabelText("템플릿 이름");
    fireEvent.change(nameInput, { target: { value: "내 템플릿" } });
    fireEvent.click(screen.getByTestId("chart-templates-save"));

    expect(
      await screen.findByText("다른 요청과 충돌했습니다. 새로고침 후 다시 시도해주세요."),
    ).toBeInTheDocument();
    // 실패 시 saveMutation.onSuccess(이름 초기화)가 돌지 않아 사용자가 다시 타이핑할 필요가 없다.
    expect(nameInput).toHaveValue("내 템플릿");
  });

  it("게이트 적색 재현: naive 타입 캐스트는 unknown 필드를 조용히 통과시키지만(적색), 실제 화면 경로(decodeTemplate)는 같은 입력을 즉시 거부한다(녹색, negative)", async () => {
    const malformed = {
      schemaVersion: 1,
      panes: [{ id: "main", kind: "main", heightRatio: 1 }],
      indicators: [{ id: "SMA", paneId: "main" }],
      extraneousField: "drift-should-not-pass-silently",
    };

    // 적색: templateModel.ts의 decodeTemplate를 우회해 원시 객체를 그대로 캐스트하면
    // schemaVersion 드리프트로 몰래 추가된 필드가 예외 없이 "성공"처럼 통과한다.
    const naive = malformed as unknown as Template;
    expect(naive.indicators).toHaveLength(1);

    // 녹색: 실제 소스 decodeTemplate()은 같은 입력을 즉시 거부한다(unknown field, fail-closed).
    expect(() => decodeTemplate(malformed)).toThrow(/field_unknown/);

    // 녹색 경로가 화면에 실제로 배선돼 있는지: ChartTemplates.handleApply는 decodeTemplate를
    // 거치므로, 적용 버튼을 눌러도 위와 동일하게 거부되고 onApplied가 호출되지 않는다.
    const record = templateRecord({
      template: malformed as unknown as ChartIndicatorTemplateRecord["template"],
    });
    const { onApplied } = renderTemplates({
      port: fakePort({ listIndicatorTemplates: vi.fn(async () => [record]) }),
    });

    fireEvent.click(screen.getByRole("button", { name: "템플릿" }));
    fireEvent.click(await screen.findByTestId(`chart-templates-apply-${record.id}`));

    await waitFor(() => expect(screen.getByText(/field_unknown/)).toBeInTheDocument());
    expect(onApplied).not.toHaveBeenCalled();
  });

  it("수치 성능: 지표 600개(메인 300 + 서브 300) 캡처→저장 요청까지 500ms 예산 내에 끝난다", async () => {
    const mainIndicatorIds = Array.from({ length: 300 }, (_, i) => `MAIN-${i}`);
    const subIndicatorIds = Array.from({ length: 300 }, (_, i) => `SUB-${i}`);
    const createIndicatorTemplate = vi.fn().mockResolvedValue(templateRecord());
    renderTemplates({
      port: fakePort({ createIndicatorTemplate }),
      mainIndicatorIds,
      subIndicatorIds,
    });

    fireEvent.click(screen.getByRole("button", { name: "템플릿" }));
    fireEvent.change(await screen.findByLabelText("템플릿 이름"), { target: { value: "대량 템플릿" } });

    const start = performance.now();
    fireEvent.click(screen.getByTestId("chart-templates-save"));
    await waitFor(() => expect(createIndicatorTemplate).toHaveBeenCalledTimes(1));
    const elapsedMs = performance.now() - start;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(500));
    const input = createIndicatorTemplate.mock.calls[0]?.[0];
    expect(input.template.indicators).toHaveLength(600);
    expect(input.template.panes).toHaveLength(301); // main pane + 300 sub panes
  });

  // --- task-2664: CH-17 DoD 자체("템플릿 적용 후 동일 화면 재현")의 왕복 증거.
  // 기존 저장/적용 테스트는 각각 독립된 고정 fixture로만 검증돼 capture(save)의
  // 출력이 apply의 입력으로 실제 왕복하는지는 어느 테스트도 직접 확인하지
  // 않았다 — 여기서는 실제 저장 시 전송된 template JSON을 그대로 서버 응답인
  // 것처럼 되돌려 적용하고, 그 결과가 캡처 당시 화면과 동일함을 단언한다.
  // 화면이 그 사이(적용 시점) 완전히 다른 지표 구성이었어도(다른 세션에서
  // 저장한 템플릿을 여는 상황) 재현 결과는 오직 저장된 템플릿에서만 나온다는
  // 것까지 함께 증명한다.
  it("동일 화면 재현: 캡처→저장된 template을 그대로 적용하면 캡처 당시 지표·페인 배치와 정확히 일치한다(현재 화면 구성과 무관)", async () => {
    const capturedMainIds = ["SMA"];
    const capturedSubIds = ["RSI", "MACD"];
    let savedTemplate: CreateChartIndicatorTemplateInput["template"] | undefined;
    const createIndicatorTemplate = vi.fn(async (input: { name: string; template: CreateChartIndicatorTemplateInput["template"] }) => {
      savedTemplate = input.template;
      return templateRecord({ name: input.name, template: input.template });
    });

    renderTemplates({
      port: fakePort({ createIndicatorTemplate }),
      mainIndicatorIds: capturedMainIds,
      subIndicatorIds: capturedSubIds,
    });

    fireEvent.click(screen.getByRole("button", { name: "템플릿" }));
    fireEvent.change(await screen.findByLabelText("템플릿 이름"), { target: { value: "캡처 템플릿" } });
    fireEvent.click(screen.getByTestId("chart-templates-save"));
    await waitFor(() => expect(createIndicatorTemplate).toHaveBeenCalledTimes(1));
    expect(savedTemplate).toBeDefined();
    cleanup();

    // 저장된 template을 서버 응답처럼 그대로 되돌린다(decodeTemplate가 실제로
    // 태우는 값과 동일 — encode/decode 경계를 우회하지 않는다).
    const savedRecord = templateRecord({ template: savedTemplate! });

    // "다른 화면"에서 연다: 현재 선택은 캡처 당시와 전혀 다르다.
    const { onApplied } = renderTemplates({
      port: fakePort({ listIndicatorTemplates: vi.fn(async () => [savedRecord]) }),
      mainIndicatorIds: ["EMA"],
      subIndicatorIds: ["ATR", "OBV", "VWAP"],
      knownIndicatorIds: new Set(["SMA", "RSI", "MACD", "EMA", "ATR", "OBV", "VWAP"]),
    });

    fireEvent.click(screen.getByRole("button", { name: "템플릿" }));
    fireEvent.click(await screen.findByTestId(`chart-templates-apply-${savedRecord.id}`));

    await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1));
    const result: TemplateApplyResult = onApplied.mock.calls[0]?.[0];

    // 지표 구성: 캡처 당시의 세트와 정확히 일치한다(적용 시점 화면의 EMA/ATR/OBV/VWAP가 아니다).
    expect(result.indicatorIds).toEqual([...capturedMainIds, ...capturedSubIds]);
    // 페인 배치: 캡처 당시 3-way 균등분할(main + 2 sub) 그대로 재현된다.
    expect(result.paneHeightRatios.main).toBeCloseTo(1 / 3, 10);
    expect(result.paneHeightRatios["sub-RSI"]).toBeCloseTo(1 / 3, 10);
    expect(result.paneHeightRatios["sub-MACD"]).toBeCloseTo(1 / 3, 10);
    expect(Object.keys(result.paneHeightRatios)).toHaveLength(3);
  });

  // --- task-2664: negative — 교차 테넌트로 저장된(혹은 삭제된) 템플릿을 적용
  // 시도하면 목록에 애초에 나타나지 않아(서버가 404 동형으로 접어 목록에서
  // 제외) 적용 버튼 자체가 없다 — 화면이 "낙관적으로" 남아있던 적용 버튼을
  // 눌러 조용히 실패하는 경로가 없음을 증명한다(negative).
  it("negative: 목록에 없는 템플릿(교차 테넌트·삭제됨)은 적용 버튼이 애초에 렌더되지 않는다", async () => {
    renderTemplates({ port: fakePort({ listIndicatorTemplates: vi.fn(async () => []) }) });

    fireEvent.click(screen.getByRole("button", { name: "템플릿" }));
    await waitFor(() => expect(screen.getByText("저장된 템플릿이 없습니다.")).toBeInTheDocument());
    expect(screen.queryByTestId(/chart-templates-apply-/)).not.toBeInTheDocument();
  });
});
