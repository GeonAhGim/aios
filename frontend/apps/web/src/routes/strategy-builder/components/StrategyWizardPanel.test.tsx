import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import type { GeneratedConditions } from "@aios/shared-types";
import { StrategyWizardPanel } from "./StrategyWizardPanel";

const generateWizardMutateAsync = vi.fn();
const generateFromPromptMutateAsync = vi.fn();

vi.mock("@aios/shared-hooks", () => ({
  useGenerateWizardStrategy: () => ({ mutateAsync: generateWizardMutateAsync, isPending: false }),
  useGenerateFromPrompt: () => ({ mutateAsync: generateFromPromptMutateAsync, isPending: false }),
}));

afterEach(() => {
  cleanup();
  generateWizardMutateAsync.mockReset();
  generateFromPromptMutateAsync.mockReset();
});

const GENERATED: GeneratedConditions = {
  entryConditions: [],
  exitConditions: [],
  stopLossConditions: [],
  entryCombine: "AND",
  exitCombine: "AND",
  stopLossCombine: "AND",
  explanation: "RSI 과매도 반등 매수",
};

// ADR-2026-08-29 §3: 마법사 모드가 기본이며 생성 결과는 ConditionGroup으로 넘길
// onApply만 호출한다 — 여기서 직접 저장하지 않는다(task-628과 같은 원칙).
describe("StrategyWizardPanel 마법사 모드", () => {
  it("기본은 마법사 모드이며 목표·위험 허용도 선택지를 보여준다", () => {
    render(<StrategyWizardPanel onApply={vi.fn()} />);

    expect(screen.getByText("투자 목표")).toBeInTheDocument();
    expect(screen.getByText("위험 허용도")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/RSI 과매도에서/)).not.toBeInTheDocument();
  });

  it("생성하기 클릭 시 선택한 goal·riskTolerance로 generateWizard.mutateAsync를 호출한다", async () => {
    generateWizardMutateAsync.mockResolvedValue(GENERATED);
    render(<StrategyWizardPanel onApply={vi.fn()} />);

    fireEvent.change(screen.getByText("투자 목표").closest("label")!.querySelector("select")!, {
      target: { value: "AGGRESSIVE_GROWTH" },
    });
    fireEvent.change(screen.getByText("위험 허용도").closest("label")!.querySelector("select")!, {
      target: { value: "HIGH" },
    });
    fireEvent.click(screen.getByRole("button", { name: "생성하기" }));

    await waitFor(() =>
      expect(generateWizardMutateAsync).toHaveBeenCalledWith({
        goal: "AGGRESSIVE_GROWTH",
        riskTolerance: "HIGH",
      }),
    );
  });

  it("생성 결과의 explanation을 보여주고 적용 버튼 클릭 시 onApply(result)를 호출한다", async () => {
    generateWizardMutateAsync.mockResolvedValue(GENERATED);
    const onApply = vi.fn();
    render(<StrategyWizardPanel onApply={onApply} />);

    fireEvent.click(screen.getByRole("button", { name: "생성하기" }));

    await waitFor(() => expect(screen.getByText("RSI 과매도 반등 매수")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "이 조건 적용하기" }));

    expect(onApply).toHaveBeenCalledWith(GENERATED);
  });
});

describe("StrategyWizardPanel AI 프롬프트 모드", () => {
  it("AI 프롬프트 탭으로 전환하면 프롬프트 입력창을 보여주고 비어있으면 생성하기가 비활성화된다", () => {
    render(<StrategyWizardPanel onApply={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "AI 프롬프트" }));

    expect(
      screen.getByPlaceholderText("예: RSI 과매도에서 반등 매수하고 과매수에서 매도하는 전략"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "생성하기" })).toBeDisabled();
  });

  it("프롬프트 입력 후 생성하기 클릭 시 generateFromPrompt.mutateAsync를 호출한다", async () => {
    generateFromPromptMutateAsync.mockResolvedValue(GENERATED);
    render(<StrategyWizardPanel onApply={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "AI 프롬프트" }));
    fireEvent.change(
      screen.getByPlaceholderText("예: RSI 과매도에서 반등 매수하고 과매수에서 매도하는 전략"),
      { target: { value: "RSI 과매도 매수" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "생성하기" }));

    await waitFor(() =>
      expect(generateFromPromptMutateAsync).toHaveBeenCalledWith({ prompt: "RSI 과매도 매수" }),
    );
  });

  it("모드를 전환하면 기존 결과·에러를 초기화한다", async () => {
    generateWizardMutateAsync.mockResolvedValue(GENERATED);
    render(<StrategyWizardPanel onApply={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "생성하기" }));
    await waitFor(() => expect(screen.getByText("RSI 과매도 반등 매수")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "AI 프롬프트" }));

    expect(screen.queryByText("RSI 과매도 반등 매수")).not.toBeInTheDocument();
  });
});

// spec §3.3 에러 taxonomy: 생성 실패는 err.message를 직접 노출하지 않고
// routeApiError로 판정해 400/403/그 외를 각각 BadRequestNotice/ForbiddenNotice/
// ErrorMessage 경로로만 보여준다(task-901 패턴).
describe("StrategyWizardPanel 생성 실패 에러 표시", () => {
  it("negative: VALIDATION_*(400) 실패는 err.message 대신 BadRequestNotice의 매핑 문구를 보여준다", async () => {
    generateWizardMutateAsync.mockRejectedValue(
      new ApiError(400, "raw server detail", undefined, "VALIDATION_IDEMPOTENCY_KEY_REQUIRED"),
    );
    render(<StrategyWizardPanel onApply={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "생성하기" }));

    await waitFor(() =>
      expect(
        screen.getByText("요청이 올바르지 않습니다. 새로고침 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: POLICY_*(403) 거부는 err.message 대신 ForbiddenNotice의 매핑 문구를 보여준다", async () => {
    generateWizardMutateAsync.mockRejectedValue(
      new ApiError(403, "raw server detail", "trace-1", "POLICY_LIVE_BLOCKED"),
    );
    render(<StrategyWizardPanel onApply={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "생성하기" }));

    await waitFor(() =>
      expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    generateWizardMutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    render(<StrategyWizardPanel onApply={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "생성하기" }));

    await waitFor(() => expect(screen.getByText("생성에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });
});
