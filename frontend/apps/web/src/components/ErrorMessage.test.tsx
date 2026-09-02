import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ErrorMessage } from "./ErrorMessage";

describe("ErrorMessage", () => {
  it("알려진 error_code는 고정된 한국어 메시지로 보여준다", () => {
    render(<ErrorMessage errorCode="AUTH_REQUIRED" message="raw server message" />);

    expect(screen.getByText("로그인이 필요합니다.")).toBeInTheDocument();
    expect(screen.queryByText("raw server message")).not.toBeInTheDocument();
  });

  it("표에 없는 코드는 접두(prefix) 계열 안내로 대체한다", () => {
    render(<ErrorMessage errorCode="RISK_MAX_DRAWDOWN_EXCEEDED" />);

    expect(screen.getByText("위험 관리 정책에 의해 거부된 요청입니다.")).toBeInTheDocument();
  });

  it("코드가 없거나 매핑이 없으면 서버 message로 대체한다", () => {
    render(<ErrorMessage errorCode={null} message="서버가 준 원문 메시지" />);

    expect(screen.getByText("서버가 준 원문 메시지")).toBeInTheDocument();
  });

  it("코드도 message도 없으면 기본 안내 문구를 보여준다", () => {
    render(<ErrorMessage />);

    expect(
      screen.getByText("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요."),
    ).toBeInTheDocument();
  });

  it("traceId가 있으면 문의용 참조번호를 함께 보여주고, 없으면 생략한다", () => {
    const { rerender } = render(<ErrorMessage errorCode="INTERNAL_ERROR" traceId="trace-123" />);
    expect(screen.getByText("문의 시 참조번호: trace-123")).toBeInTheDocument();

    rerender(<ErrorMessage errorCode="INTERNAL_ERROR" />);
    expect(screen.queryByText(/문의 시 참조번호/)).not.toBeInTheDocument();
  });
});
