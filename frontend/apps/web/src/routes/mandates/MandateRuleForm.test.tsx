import "../../i18n";
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

describe("MandateRuleForm negative/실패 계열", () => {
  // decision: "판정은 서버 권위, CM-A5" — 이 폼은 규칙 의미를 재구현하지 않으므로
  // 잘못된 값도 프론트가 거부하지 않고 그대로 onSubmit에 전달해야 한다(서버가 거부).
  it("총 노출 한도에 음수를 입력해도 클라이언트가 거부하지 않고 그대로 onSubmit에 전달한다", () => {
    const onSubmit = vi.fn();
    render(<MandateRuleForm submitLabel="규칙 생성" pending={false} onSubmit={onSubmit} />);

    const [totalExposure] = numberInputs();
    fireEvent.change(totalExposure, { target: { value: "-10" } });
    fireEvent.click(screen.getByRole("button", { name: "규칙 생성" }));

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ maxTotalExposurePct: -10 }));
  });

  it("필수 숫자 필드에 숫자가 아닌 값을 넣으면 input이 빈 값으로 무너지고 0이 그대로 onSubmit에 전달된다", () => {
    const onSubmit = vi.fn();
    render(<MandateRuleForm submitLabel="규칙 생성" pending={false} onSubmit={onSubmit} />);

    const [totalExposure] = numberInputs();
    // input[type=number]은 숫자가 아닌 값을 빈 문자열로 무너뜨리고, 폼은 이를 보정하지 않는다.
    fireEvent.change(totalExposure, { target: { value: "abc" } });
    fireEvent.click(screen.getByRole("button", { name: "규칙 생성" }));

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ maxTotalExposurePct: 0 }));
  });

  it("허용 자율성 select가 값 없이 제출돼도 폼은 크래시하지 않고 기본 옵션값을 전달한다", () => {
    const onSubmit = vi.fn();
    render(<MandateRuleForm submitLabel="규칙 생성" pending={false} onSubmit={onSubmit} />);

    fireEvent.click(screen.getByRole("button", { name: "규칙 생성" }));

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ allowedAutonomy: "OBSERVE" }));
  });

  it("저장 mutation이 실패해도(onSubmit이 실패를 나타내는 값으로 응답) 폼은 입력값을 롤백하지 않고 그대로 보존한다", () => {
    // 이 컴포넌트는 저장 성공/실패를 모르는 순수 표시용 폼이다(성공/실패 판정은 상위
    // 컨테이너의 mutation이 담당) — 실패 응답이 와도 로컬 rules/forbiddenAssetsText
    // state를 되돌리는 로직이 없으므로, 제출 후에도 사용자가 입력한 값이 유지돼야 한다.
    const onSubmit = vi.fn().mockReturnValue({ ok: false, error: "save rejected" });
    render(<MandateRuleForm submitLabel="규칙 생성" pending={false} onSubmit={onSubmit} />);

    const [totalExposure] = numberInputs();
    fireEvent.change(totalExposure, { target: { value: "80" } });
    fireEvent.click(screen.getByRole("button", { name: "규칙 생성" }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    // 실패 응답으로 폼 상태가 초기화(롤백)되지 않고 사용자가 입력한 값을 그대로 유지한다.
    expect(numberInputs()[0]).toHaveValue(80);
  });
});
