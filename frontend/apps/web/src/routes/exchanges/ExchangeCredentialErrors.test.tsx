import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { RegisterCredentialError, RevokeCredentialError } from "./ExchangeCredentialErrors";

afterEach(() => cleanup());

// task-943: VALIDATION_INVALID_FIELD는 classifyBadRequest가 "field"로 분류해
// BadRequestNotice가 null을 렌더한다(task-364 설계) — RegisterCredentialError는 그
// 경우에도 fieldErrors를 ErrorMessage로 넘겨 인라인 오류가 그려지게 한다.
describe("RegisterCredentialError", () => {
  it("403(POLICY_*)는 ForbiddenNotice 문구를 보여준다", () => {
    render(
      <RegisterCredentialError
        error={new ApiError(403, "raw detail", undefined, "POLICY_LIVE_BLOCKED")}
        onRetry={vi.fn()}
        fieldErrors={{}}
      />,
    );

    expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument();
    expect(screen.queryByText("raw detail")).not.toBeInTheDocument();
  });

  it("400 필드 분류(field)는 BadRequestNotice 대신 fieldErrors를 ErrorMessage로 넘긴다", () => {
    render(
      <RegisterCredentialError
        error={new ApiError(400, "요청 값이 올바르지 않습니다.", undefined, "VALIDATION_INVALID_FIELD")}
        onRetry={vi.fn()}
        fieldErrors={{ api_key: "요청 값이 올바르지 않습니다." }}
      />,
    );

    expect(screen.getByText("요청 값이 올바르지 않습니다.")).toBeInTheDocument();
  });

  it("알려진 400(field가 아님)은 BadRequestNotice의 매핑 문구를 보여준다", () => {
    render(
      <RegisterCredentialError
        error={new ApiError(400, "raw", undefined, "VALIDATION_IDEMPOTENCY_KEY_REQUIRED")}
        onRetry={vi.fn()}
        fieldErrors={{}}
      />,
    );

    expect(
      screen.getByText("요청이 올바르지 않습니다. 새로고침 후 다시 시도해주세요."),
    ).toBeInTheDocument();
    expect(screen.queryByText("raw")).not.toBeInTheDocument();
  });

  it("Error.message에 비밀이 반향되면 redactSecret으로 가려서 보여준다", () => {
    render(
      <RegisterCredentialError
        error={new Error('invalid field api_secret="leaked-secret-value-0001"')}
        onRetry={vi.fn()}
        fieldErrors={{}}
      />,
    );

    expect(screen.queryByText(/leaked-secret-value-0001/)).not.toBeInTheDocument();
    expect(screen.getByText(/api_secret=\[REDACTED\]/)).toBeInTheDocument();
  });

  it("negative: ApiError가 아닌 실패는 message를 그대로(비밀 패턴이 없으면) 보여준다", () => {
    render(
      <RegisterCredentialError error={new Error("network failure")} onRetry={vi.fn()} fieldErrors={{}} />,
    );

    expect(screen.getByText("network failure")).toBeInTheDocument();
  });

  it("negative: 5xx 재시도 가능 에러는 다시 시도 버튼을 보여준다", () => {
    render(
      <RegisterCredentialError
        error={new ApiError(503, "raw", undefined, "EXCHANGE_UNAVAILABLE")}
        onRetry={vi.fn()}
        fieldErrors={{}}
      />,
    );

    expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
  });
});

describe("RevokeCredentialError", () => {
  it("403(POLICY_*)는 ForbiddenNotice 문구를 보여준다", () => {
    render(
      <RevokeCredentialError
        error={new ApiError(403, "blocked", undefined, "POLICY_LIVE_BLOCKED")}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument();
  });

  it("알려진 400은 BadRequestNotice의 매핑 문구를 보여주고 원문은 노출하지 않는다", () => {
    render(
      <RevokeCredentialError
        error={new ApiError(400, "raw detail", undefined, "VALIDATION_IDEMPOTENCY_KEY_REQUIRED")}
        onRetry={vi.fn()}
      />,
    );

    expect(
      screen.getByText("요청이 올바르지 않습니다. 새로고침 후 다시 시도해주세요."),
    ).toBeInTheDocument();
    expect(screen.queryByText("raw detail")).not.toBeInTheDocument();
  });

  it("negative: ApiError가 아닌 실패는 message를 그대로(비밀 패턴이 없으면) 보여준다", () => {
    render(<RevokeCredentialError error={new Error("network failure")} onRetry={vi.fn()} />);

    expect(screen.getByText("network failure")).toBeInTheDocument();
  });

  it("traceId가 있으면 지원코드를 함께 보여준다", () => {
    render(
      <RevokeCredentialError
        error={new ApiError(500, "internal", "trace-revoke-1", "INTERNAL_ERROR")}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByText("지원코드: trace-revoke-1")).toBeInTheDocument();
  });
});
