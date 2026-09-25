import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { ConnectionWizardPage } from "./ConnectionWizardPage";

const RAW_SECRET_KEY = "sk_live_super_secret_1234";

const beginConnectionMutate = vi.fn();
vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
  useBeginConnection: () => ({ mutate: beginConnectionMutate, isPending: false }),
}));

afterEach(() => {
  cleanup();
  beginConnectionMutate.mockReset();
});

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/onboarding/connect"]}>
      <ConnectionWizardPage />
    </MemoryRouter>,
  );
}

function fillProviderStep(key = RAW_SECRET_KEY) {
  fireEvent.change(screen.getByLabelText("공급자 코드"), { target: { value: "BITGET" } });
  fireEvent.change(screen.getByLabelText("계정 키(opaque_account_ref)"), { target: { value: key } });
  fireEvent.click(screen.getByText("READ_BALANCE"));
  fireEvent.click(screen.getByText("다음"));
}

function confirmReadonlyStep() {
  fireEvent.click(screen.getByLabelText("읽기 전용 권한만 요청됨을 확인했습니다."));
  fireEvent.click(screen.getByRole("button", { name: "다음" }));
}

describe("ConnectionWizardPage", () => {
  it("negative: 1단계 필드를 채우기 전에는 다음 버튼이 비활성화된다", () => {
    renderPage();
    expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  });

  it("negative: 읽기 전용 확인 체크박스를 누르기 전에는 2단계에서 다음으로 진행할 수 없다", () => {
    renderPage();
    fillProviderStep();
    expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  });

  it("3단계까지 진행하면 키가 마스킹되어 표시되고 원문은 어디에도 나타나지 않는다", () => {
    renderPage();
    fillProviderStep();
    confirmReadonlyStep();

    const masked = screen.getByTestId("wizard-masked-key");
    expect(masked).toHaveTextContent("1234");
    expect(masked.textContent).not.toBe(RAW_SECRET_KEY);
    expect(screen.queryByText(RAW_SECRET_KEY)).not.toBeInTheDocument();
  });

  it("제출하면 opaqueAccountRef·providerCode·requestedCapabilityProfile 외 필드 없이 begin API를 호출한다(테넌트/부가필드 주입 불가)", () => {
    renderPage();
    fillProviderStep();
    confirmReadonlyStep();
    fireEvent.click(screen.getByRole("button", { name: "연결하기" }));

    expect(beginConnectionMutate).toHaveBeenCalledTimes(1);
    const [payload] = beginConnectionMutate.mock.calls[0];
    expect(Object.keys(payload).sort()).toEqual(
      ["opaqueAccountRef", "providerCode", "requestedCapabilityProfile"].sort(),
    );
    expect(payload.providerCode).toBe("BITGET");
  });

  it("negative: 키를 제출해도 console에 원문 키가 평문으로 로그되지 않는다", () => {
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    renderPage();
    fillProviderStep();
    confirmReadonlyStep();
    fireEvent.click(screen.getByRole("button", { name: "연결하기" }));

    for (const spy of [logSpy, warnSpy, errorSpy]) {
      for (const call of spy.mock.calls) {
        for (const arg of call) {
          expect(String(arg)).not.toContain(RAW_SECRET_KEY);
        }
      }
    }
    logSpy.mockRestore();
    warnSpy.mockRestore();
    errorSpy.mockRestore();
  });

  it("negative: 403 응답은 raw message 대신 ForbiddenNotice 매핑 문구로 표시된다", () => {
    beginConnectionMutate.mockImplementation((_vars, { onError }) => {
      onError(new ApiError(403, "raw server detail", undefined, "AUTH_FORBIDDEN"));
    });
    renderPage();
    fillProviderStep();
    confirmReadonlyStep();
    fireEvent.click(screen.getByRole("button", { name: "연결하기" }));

    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("연결 요청이 성공하면 성공 안내와 연결 목록 이동 링크를 보여준다", () => {
    beginConnectionMutate.mockImplementation((_vars, { onSuccess }) => {
      onSuccess({ id: "conn-1" });
    });
    renderPage();
    fillProviderStep();
    confirmReadonlyStep();
    fireEvent.click(screen.getByRole("button", { name: "연결하기" }));

    expect(screen.getByRole("link", { name: "연결 목록으로 이동" })).toHaveAttribute(
      "href",
      "/settings/connections",
    );
  });
});
