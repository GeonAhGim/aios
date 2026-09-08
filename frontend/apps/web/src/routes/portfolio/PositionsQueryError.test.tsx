import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { PositionsQueryError } from "./PositionsQueryError";

afterEach(() => cleanup());

// task-1524(LB-19) §3.3 taxonomy 갈래: 404→NotFoundState(재시도 없음), 403→ForbiddenNotice,
// 그 외(재시도 가능한 5xx 등)→ErrorMessage. 새 분류기를 만들지 않고 기존
// routeApiError/classifyForbidden만 경유한다.
describe("PositionsQueryError 404", () => {
  it("RESOURCE_NOT_FOUND는 notFoundTitle로 NotFoundState를 보여주고 재시도 버튼이 없다", () => {
    render(
      <PositionsQueryError
        error={new ApiError(404, "raw detail", undefined, "RESOURCE_NOT_FOUND")}
        notFoundTitle="포지션을 찾을 수 없습니다."
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByText("포지션을 찾을 수 없습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
    expect(screen.queryByText("raw detail")).not.toBeInTheDocument();
  });
});

describe("PositionsQueryError 403", () => {
  it("AUTH_TENANT_MISMATCH는 ForbiddenNotice 문구를 보여준다", () => {
    render(
      <PositionsQueryError
        error={new ApiError(403, "raw detail", undefined, "AUTH_TENANT_MISMATCH")}
        notFoundTitle="포지션을 찾을 수 없습니다."
      />,
    );

    expect(screen.getByText("이 리소스에 접근할 권한이 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText("raw detail")).not.toBeInTheDocument();
  });
});

describe("PositionsQueryError 그 외(ErrorMessage)", () => {
  it("negative: 5xx(재시도 가능)는 ErrorMessage + 다시 시도 버튼을 보여주고 클릭 시 onRetry를 호출한다", () => {
    const onRetry = vi.fn();
    render(
      <PositionsQueryError
        error={new ApiError(503, "raw", undefined, "EXCHANGE_UNAVAILABLE")}
        notFoundTitle="포지션을 찾을 수 없습니다."
        onRetry={onRetry}
      />,
    );

    expect(
      screen.getByText("거래소 연결이 원활하지 않습니다. 잠시 후 다시 시도해주세요."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("negative: 재시도 불가 코드(INTERNAL_ERROR)는 다시 시도 버튼을 보여주지 않는다", () => {
    render(
      <PositionsQueryError
        error={new ApiError(500, "raw", undefined, "INTERNAL_ERROR")}
        notFoundTitle="포지션을 찾을 수 없습니다."
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });

  it("traceId가 있으면 지원코드를 함께 보여준다", () => {
    render(
      <PositionsQueryError
        error={new ApiError(500, "raw", "trace-pos-1", "INTERNAL_ERROR")}
        notFoundTitle="포지션을 찾을 수 없습니다."
      />,
    );

    expect(screen.getByText("지원코드: trace-pos-1")).toBeInTheDocument();
  });
});
