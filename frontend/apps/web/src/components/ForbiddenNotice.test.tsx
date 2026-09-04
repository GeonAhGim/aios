import "@testing-library/jest-dom/vitest";
import { buildApiError } from "@aios/api-client";
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

  it("POLICY_*(403): 정책 거부 안내 + DenialReasons 사유 목록을 함께 보여준다(task-388: 진짜 ApiError 배선)", () => {
    // mock 객체가 아니라 buildApiError로 만든 실제 ApiError 인스턴스를 넘겨서, http.ts가
    // 봉투 error.details를 실제로 전달하고 DenialReasons(extractReasonCodes)가 그 details를
    // 읽어 렌더까지 이어지는지를 검증한다.
    const error = buildApiError(
      403,
      {
        error_code: "POLICY_LIVE_BLOCKED",
        message: "실거래 모드에서는 허용되지 않는 작업입니다.",
        details: { reason_codes: ["POLICY_LIVE_BLOCKED", "RISK_MAX_DRAWDOWN_EXCEEDED"] },
        trace_id: "trace-1",
        retry_after_seconds: null,
      },
      undefined,
      undefined,
    );

    render(<ForbiddenNotice error={error} />);

    // 배너 <p>와 DenialReasons <li> 둘 다 같은 문구를 렌더하므로(EXACT_MESSAGES와
    // REASON_CODE_MESSAGES가 같은 코드에 같은 문장을 매핑) getByText는 쓸 수 없다 —
    // 목록 안에서 사유별 문구를 각각 단언해 실제로 reason_codes가 파싱됐는지 확인한다.
    const denialReasonsList = screen.getByRole("list");
    expect(denialReasonsList).toHaveTextContent("실거래 모드에서는 허용되지 않는 작업입니다.");
    expect(denialReasonsList).toHaveTextContent("최대 손실 한도를 초과하여 거부되었습니다.");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("AUTHZ_FORBIDDEN·미지 403 코드: forbidden으로 폴백해 권한 없음 안내를 보여준다(throw 없음)", () => {
    render(<ForbiddenNotice error={{ statusCode: 403, errorCode: "AUTHZ_SOME_FUTURE_CODE" }} />);

    expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("담당 에러(403)인데 errorCode/message가 모두 없어도 기본 안내 문구로 폴백한다(무응답 아님, task-1231)", () => {
    render(<ForbiddenNotice error={{ statusCode: 403 }} />);

    expect(
      screen.getByText("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요."),
    ).toBeInTheDocument();
  });

  it("POLICY_*인데 details.reason_codes가 비어있어도 DenialReasons만 비고 메인 배너 문구는 그대로 보여준다(무응답 아님, task-1231)", () => {
    render(
      <ForbiddenNotice
        error={{ statusCode: 403, errorCode: "POLICY_LIVE_BLOCKED", details: { reason_codes: [] } }}
      />,
    );

    expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
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
