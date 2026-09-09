import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MandateRuleForm } from "./MandateRuleForm";

afterEach(() => cleanup());

function numberInputs(): HTMLInputElement[] {
  return screen.getAllByRole("spinbutton") as HTMLInputElement[];
}

describe("MandateRuleForm 정상 렌더·제출", () => {
  it("기본값으로 6개 규칙 필드를 렌더하고 submitLabel을 버튼에 보여준다", () => {
    render(<MandateRuleForm submitLabel="규칙 생성" pending={false} onSubmit={vi.fn()} />);

    expect(screen.getByText("총 노출 한도(%)")).toBeInTheDocument();
    expect(screen.getByText("허용 자율성")).toBeInTheDocument();
    const [totalExposure] = numberInputs();
    expect(totalExposure).toHaveValue(50);
    expect(screen.getByRole("button", { name: "규칙 생성" })).toBeInTheDocument();
  });

  it("필드를 바꾸고 제출하면 변경된 값으로 onSubmit을 호출한다", () => {
    const onSubmit = vi.fn();
    render(<MandateRuleForm submitLabel="규칙 생성" pending={false} onSubmit={onSubmit} />);

    const [totalExposure] = numberInputs();
    fireEvent.change(totalExposure, { target: { value: "80" } });
    fireEvent.change(screen.getByPlaceholderText("BTC, ETH"), { target: { value: "SOL, DOGE" } });
    fireEvent.click(screen.getByRole("button", { name: "규칙 생성" }));

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ maxTotalExposurePct: 80, forbiddenAssets: ["SOL", "DOGE"] }),
    );
  });
});

describe("MandateRuleForm 경계 입력", () => {
  it("금지 자산에 빈 항목·앞뒤 공백이 섞여도 trim 후 빈 문자열을 제거해 전달한다", () => {
    const onSubmit = vi.fn();
    render(<MandateRuleForm submitLabel="규칙 생성" pending={false} onSubmit={onSubmit} />);

    fireEvent.change(screen.getByPlaceholderText("BTC, ETH"), {
      target: { value: " BTC ,, ETH ,  ," },
    });
    fireEvent.click(screen.getByRole("button", { name: "규칙 생성" }));

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ forbiddenAssets: ["BTC", "ETH"] }));
  });
});

describe("MandateRuleForm 거부 입력(pending)", () => {
  it("pending=true면 제출 버튼이 로딩·비활성 상태가 되고 클릭해도 onSubmit이 호출되지 않는다", () => {
    const onSubmit = vi.fn();
    render(<MandateRuleForm submitLabel="규칙 생성" pending onSubmit={onSubmit} />);

    const submitButton = screen.getByRole("button", { name: "규칙 생성" });
    expect(submitButton).toBeDisabled();

    fireEvent.click(submitButton);

    expect(onSubmit).not.toHaveBeenCalled();
  });
});
