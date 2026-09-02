import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorMessage } from "./ErrorMessage";

afterEach(() => cleanup());

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

  it("traceId가 있으면 지원코드를 함께 보여주고, 없으면 생략한다", () => {
    const { rerender } = render(<ErrorMessage errorCode="INTERNAL_ERROR" traceId="trace-123" />);
    expect(screen.getByText("지원코드: trace-123")).toBeInTheDocument();

    rerender(<ErrorMessage errorCode="INTERNAL_ERROR" />);
    expect(screen.queryByText(/지원코드/)).not.toBeInTheDocument();
  });

  it("RATE_LIMIT_EXCEEDED가 아니면 onRetry가 있어도 재시도 버튼을 보여주지 않는다", () => {
    render(<ErrorMessage errorCode="INTERNAL_ERROR" onRetry={vi.fn()} retryAfterSec={10} />);
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });

  // spec §9 PLT-25: retryAfterSec이 없으면(둘 다 없음 케이스) 재시도 버튼이
  // 카운트다운 없이 즉시 활성화된다.
  it("RATE_LIMIT_EXCEEDED이고 retryAfterSec이 없으면 재시도 버튼이 즉시 활성화된다", () => {
    render(<ErrorMessage errorCode="RATE_LIMIT_EXCEEDED" onRetry={vi.fn()} />);

    const button = screen.getByRole("button", { name: "다시 시도" });
    expect(button).toBeEnabled();
    expect(screen.queryByText(/초 후 재시도 가능/)).not.toBeInTheDocument();
  });

  it("fieldErrors가 매핑되면 중복 배너를 숨긴다", () => {
    const { container } = render(
      <ErrorMessage
        errorCode="VALIDATION_INVALID_FIELD"
        fieldErrors={{ email: "이메일 형식이 올바르지 않습니다." }}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("fieldErrors가 빈 객체면 배너를 그대로 보여준다", () => {
    render(<ErrorMessage errorCode="VALIDATION_INVALID_FIELD" fieldErrors={{}} />);
    expect(screen.getByText("입력값을 확인해주세요.")).toBeInTheDocument();
  });

  it("RATE_LIMIT_EXCEEDED이고 retryAfterSec이 있으면 카운트다운 후 재시도 버튼이 활성화된다", () => {
    vi.useFakeTimers();
    try {
      const onRetry = vi.fn();
      render(<ErrorMessage errorCode="RATE_LIMIT_EXCEEDED" onRetry={onRetry} retryAfterSec={2} />);

      const button = screen.getByRole("button", { name: "다시 시도" });
      expect(button).toBeDisabled();
      expect(screen.getByText("2초 후 재시도 가능")).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(1000);
      });
      act(() => {
        vi.advanceTimersByTime(1000);
      });

      expect(button).toBeEnabled();
      expect(screen.queryByText(/초 후 재시도 가능/)).not.toBeInTheDocument();

      button.click();
      expect(onRetry).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
