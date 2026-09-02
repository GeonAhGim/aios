import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ForbiddenNotice } from "./ForbiddenNotice";

afterEach(() => cleanup());

describe("ForbiddenNotice", () => {
  it("AUTH_MFA_REQUIRED(403): step-up 안내 + onStepUp 액션 버튼을 보여준다", () => {
    const onStepUp = vi.fn();
    render(
      <ForbiddenNotice
        error={{ statusCode: 403, errorCode: "AUTH_MFA_REQUIRED", message: "추가 인증이 필요합니다." }}
        onStepUp={onStepUp}
      />,
    );

    expect(screen.getByText("추가 인증이 필요합니다.")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "step-up 인증" });
    fireEvent.click(button);
    expect(onStepUp).toHaveBeenCalledTimes(1);
  });

  it("AUTH_TENANT_MISMATCH(403): 테넌트 불일치 안내, step-up 버튼 없음", () => {
    render(<ForbiddenNotice error={{ statusCode: 403, errorCode: "AUTH_TENANT_MISMATCH" }} />);

    expect(screen.getByText("이 리소스에 접근할 권한이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("POLICY_*(403): 정책 거부 안내 + DenialReasons 사유 목록을 함께 보여준다", () => {
    render(
      <ForbiddenNotice
        error={{
          statusCode: 403,
          errorCode: "POLICY_LIVE_BLOCKED",
          details: { reason_codes: ["POLICY_LIVE_BLOCKED"] },
        }}
      />,
    );

    expect(screen.getAllByText("실거래 모드에서는 허용되지 않는 작업입니다.").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("AUTHZ_FORBIDDEN·미지 403 코드: forbidden으로 폴백해 권한 없음 안내를 보여준다(throw 없음)", () => {
    render(<ForbiddenNotice error={{ statusCode: 403, errorCode: "AUTHZ_SOME_FUTURE_CODE" }} />);

    expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("negative: 401/404 등 403이 아니면 아무것도 렌더링하지 않는다", () => {
    const { container: unauthorized } = render(
      <ForbiddenNotice error={{ statusCode: 401, errorCode: "AUTH_REQUIRED" }} />,
    );
    expect(unauthorized).toBeEmptyDOMElement();

    cleanup();

    const { container: notFound } = render(
      <ForbiddenNotice error={{ statusCode: 404, errorCode: "RESOURCE_NOT_FOUND" }} />,
    );
    expect(notFound).toBeEmptyDOMElement();
  });
});
