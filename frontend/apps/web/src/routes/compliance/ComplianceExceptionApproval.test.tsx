import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { perfBudgetMs } from "../../test/perfBudget";
import {
  ComplianceExceptionApproval,
  type ComplianceExceptionDecisionInput,
  type PendingComplianceException,
} from "./ComplianceExceptionApproval";

afterEach(() => cleanup());

function pending(overrides: Partial<PendingComplianceException> = {}): PendingComplianceException {
  return { id: "exc-1", ruleId: "RESTRICTED_LIST", message: "금지 종목 주문 시도.", ...overrides };
}

function enterReason(text = "리스크 위원회 승인 완료", ruleId = "RESTRICTED_LIST") {
  fireEvent.change(screen.getByLabelText("사유"), { target: { value: text } });
  void ruleId;
}

describe("ComplianceExceptionApproval — task-5996 CM-18 예외 승인(override)", () => {
  it("negative(빈 상태): 대기 중인 예외 승인 요청이 없으면 명시적 빈 상태를 보여준다", () => {
    render(<ComplianceExceptionApproval pendingExceptions={[]} />);

    expect(screen.getByText("대기 중인 예외 승인 요청이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText("승인")).not.toBeInTheDocument();
  });

  it("negative(I-11: 클라이언트 플래그만으로 승인 불가): onSubmit이 배선되지 않으면 승인/거부가 비활성화되고 안내를 보여준다", () => {
    render(<ComplianceExceptionApproval pendingExceptions={[pending()]} />);

    expect(
      screen.getByText("예외 승인 API가 아직 연결되지 않았습니다 — 승인/거부가 비활성화됩니다."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "승인" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "거부" })).toBeDisabled();
  });

  it("negative(입력 검증): 사유 없이 승인을 누르면 API를 호출하지 않고 검증 메시지만 보여준다", () => {
    const onSubmit = vi.fn();
    render(<ComplianceExceptionApproval pendingExceptions={[pending()]} onSubmit={onSubmit} />);

    fireEvent.click(screen.getByRole("button", { name: "승인" }));

    expect(screen.getByText("사유를 입력하세요.")).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("positive: 승인 API가 성공하면 사유와 함께 제출하고 처리 완료 상태를 보여준다", async () => {
    let resolveSubmit: () => void = () => {};
    const onSubmit = vi.fn(
      (_input: ComplianceExceptionDecisionInput) =>
        new Promise<void>((resolve) => {
          resolveSubmit = resolve;
        }),
    );
    render(<ComplianceExceptionApproval pendingExceptions={[pending()]} onSubmit={onSubmit} />);

    enterReason();
    fireEvent.click(screen.getByRole("button", { name: "승인" }));

    expect(onSubmit).toHaveBeenCalledWith({
      exceptionId: "exc-1",
      action: "APPROVE",
      reason: "리스크 위원회 승인 완료",
    });

    await act(async () => {
      resolveSubmit();
      await Promise.resolve();
    });

    expect(screen.getByText("처리 완료.")).toBeInTheDocument();
    expect(screen.getByText("APPROVE")).toBeInTheDocument();
  });

  it("negative(실패 주입): 승인/거부 API 호출이 실패하면 무음 실패 대신 에러를 화면에 노출한다", async () => {
    const onSubmit = vi.fn(() => Promise.reject(new Error("네트워크 오류")));
    render(<ComplianceExceptionApproval pendingExceptions={[pending()]} onSubmit={onSubmit} />);

    enterReason();
    fireEvent.click(screen.getByRole("button", { name: "거부" }));

    await act(async () => {
      await Promise.resolve().then(() => Promise.resolve());
    });

    expect(screen.getByText("네트워크 오류")).toBeInTheDocument();
    expect(screen.queryByText("처리 완료.")).not.toBeInTheDocument();
  });

  it("negative(I-11 교차검증): API 호출 전에는 클라이언트 상태만으로 '처리 완료'를 자칭하지 않는다", () => {
    const onSubmit = vi.fn(() => new Promise<void>(() => {}));
    render(<ComplianceExceptionApproval pendingExceptions={[pending()]} onSubmit={onSubmit} />);

    enterReason();
    fireEvent.click(screen.getByRole("button", { name: "승인" }));

    expect(screen.queryByText("처리 완료.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "승인" })).toBeDisabled();
  });

  it("성능 단언: 예외 승인 대기 목록 200건 렌더가 예산 안에 끝난다", () => {
    const many = Array.from({ length: 200 }, (_, i) => pending({ id: `exc-${i}`, ruleId: `RULE_${i}` }));

    const startedAt = performance.now();
    render(<ComplianceExceptionApproval pendingExceptions={many} />);
    expect(screen.getByText("RULE_199")).toBeInTheDocument();
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(perfBudgetMs(1000));
  });
});
