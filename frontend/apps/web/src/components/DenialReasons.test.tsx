import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DenialReasons } from "./DenialReasons";

afterEach(() => cleanup());

describe("DenialReasons", () => {
  it("POLICY_*/RISK_* details.reason_codes를 한국어 사유 목록으로 보여준다", () => {
    render(
      <DenialReasons
        error={{
          error_code: "RISK_MAX_DRAWDOWN_EXCEEDED",
          details: { reason_codes: ["POLICY_LIVE_BLOCKED", "AUTHZ_ZONE_VIOLATION"] },
        }}
      />,
    );

    expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument();
    expect(screen.getByText("허용되지 않은 영역에 대한 요청입니다.")).toBeInTheDocument();
  });

  it("negative: 미지의 사유 코드는 원문을 그대로 보여준다(throw 없음)", () => {
    render(
      <DenialReasons
        error={{
          error_code: "POLICY_UNKNOWN_FUTURE_CODE",
          details: { reason_codes: ["POLICY_UNKNOWN_FUTURE_CODE"] },
        }}
      />,
    );

    expect(screen.getByText("POLICY_UNKNOWN_FUTURE_CODE")).toBeInTheDocument();
  });

  it("negative: details 형식이 배열이 아니면 throw 없이 아무것도 렌더링하지 않는다", () => {
    expect(() =>
      render(
        <DenialReasons
          error={{
            error_code: "RISK_MAX_DRAWDOWN_EXCEEDED",
            details: { reason_codes: "not-an-array" },
          }}
        />,
      ),
    ).not.toThrow();

    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("negative: POLICY_/RISK_/AUTHZ_ 접두가 아닌 error_code는 아무것도 렌더링하지 않는다", () => {
    render(
      <DenialReasons
        error={{
          error_code: "STATE_INVALID_TRANSITION",
          details: { reason_codes: ["POLICY_LIVE_BLOCKED"] },
        }}
      />,
    );

    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });
});
