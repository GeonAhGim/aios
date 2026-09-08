import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import { ApiError } from "@aios/api-client";
import { RegisterCredentialForm } from "./RegisterCredentialForm";

afterEach(() => cleanup());

type Props = ComponentProps<typeof RegisterCredentialForm>;

function baseProps(overrides: Partial<Props> = {}): Props {
  return {
    exchange: "bitget",
    apiKey: "",
    apiSecret: "",
    apiPassphrase: "",
    fieldErrors: {},
    error: null,
    isPending: false,
    onExchangeChange: vi.fn(),
    onApiKeyChange: vi.fn(),
    onApiSecretChange: vi.fn(),
    onApiPassphraseChange: vi.fn(),
    onSubmit: vi.fn((e) => e.preventDefault()),
    onRetry: vi.fn(),
    ...overrides,
  };
}

function passwordInputs(container: HTMLElement) {
  return [...container.querySelectorAll('input[type="password"]')] as HTMLInputElement[];
}

describe("RegisterCredentialForm 필드 렌더링", () => {
  it("exchange가 bitget이면 API Passphrase 입력을 보여준다", () => {
    const { container } = render(<RegisterCredentialForm {...baseProps()} />);

    expect(passwordInputs(container)).toHaveLength(3);
  });

  it("exchange가 bitget이 아니면 API Passphrase 입력을 보여주지 않는다", () => {
    const { container } = render(
      <RegisterCredentialForm {...baseProps({ exchange: "kis" })} />,
    );

    expect(passwordInputs(container)).toHaveLength(2);
  });

  it("apiKey/apiSecret/apiPassphrase 값을 입력에 그대로 반영한다", () => {
    const { container } = render(
      <RegisterCredentialForm
        {...baseProps({ apiKey: "key-1", apiSecret: "secret-1", apiPassphrase: "pass-1" })}
      />,
    );

    const [apiKey, apiSecret, apiPassphrase] = passwordInputs(container);
    expect(apiKey.value).toBe("key-1");
    expect(apiSecret.value).toBe("secret-1");
    expect(apiPassphrase.value).toBe("pass-1");
  });
});

describe("RegisterCredentialForm 입력 변경 콜백", () => {
  it("각 입력 변경 시 해당 onChange 콜백을 호출한다", () => {
    const onApiKeyChange = vi.fn();
    const onApiSecretChange = vi.fn();
    const onApiPassphraseChange = vi.fn();
    const { container } = render(
      <RegisterCredentialForm
        {...baseProps({ onApiKeyChange, onApiSecretChange, onApiPassphraseChange })}
      />,
    );

    const [apiKey, apiSecret, apiPassphrase] = passwordInputs(container);
    fireEvent.change(apiKey, { target: { value: "k" } });
    fireEvent.change(apiSecret, { target: { value: "s" } });
    fireEvent.change(apiPassphrase, { target: { value: "p" } });

    expect(onApiKeyChange).toHaveBeenCalledWith("k");
    expect(onApiSecretChange).toHaveBeenCalledWith("s");
    expect(onApiPassphraseChange).toHaveBeenCalledWith("p");
  });

  it("거래소 선택 변경 시 onExchangeChange를 호출한다", () => {
    const onExchangeChange = vi.fn();
    render(<RegisterCredentialForm {...baseProps({ onExchangeChange })} />);

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "kis" } });

    expect(onExchangeChange).toHaveBeenCalledWith("kis");
  });

  it("제출 시 onSubmit을 호출한다", () => {
    const onSubmit = vi.fn((e: { preventDefault: () => void }) => e.preventDefault());
    // required 입력이 비어 있으면 브라우저 기본 검증이 submit 이벤트 자체를 막는다.
    render(
      <RegisterCredentialForm
        {...baseProps({ apiKey: "key-1", apiSecret: "secret-1", apiPassphrase: "pass-1", onSubmit })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "등록" }));

    expect(onSubmit).toHaveBeenCalled();
  });
});

describe("RegisterCredentialForm 상태 표시", () => {
  it("isPending이면 등록 버튼이 로딩 상태를 보여준다", () => {
    render(<RegisterCredentialForm {...baseProps({ isPending: true })} />);

    expect(screen.getByRole("button", { name: "등록" })).toBeDisabled();
  });

  it("error가 있으면 RegisterCredentialError를 보여준다", () => {
    render(
      <RegisterCredentialForm
        {...baseProps({
          error: new ApiError(403, "raw", undefined, "POLICY_LIVE_BLOCKED"),
        })}
      />,
    );

    expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument();
  });

  it("negative: error가 null이면 에러 영역을 렌더링하지 않는다", () => {
    render(<RegisterCredentialForm {...baseProps()} />);

    expect(screen.queryByText(/지원코드/)).not.toBeInTheDocument();
  });

  it("fieldErrors가 있으면 해당 Field에 오류 문구를 보여준다", () => {
    render(
      <RegisterCredentialForm
        {...baseProps({ fieldErrors: { api_key: "입력값을 확인해주세요." } })}
      />,
    );

    expect(screen.getByText("입력값을 확인해주세요.")).toBeInTheDocument();
  });
});
