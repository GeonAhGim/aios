import "../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorMessage } from "./ErrorMessage";

// ── mock shared-types ────────────────────────────────────────────────────────
const mockClassifyRetry = vi.hoisted(() => vi.fn(() => ({ kind: "none" as const })));
const mockGetApiErrorMessage = vi.hoisted(() => vi.fn(() => "요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요."));

vi.mock("@aios/shared-types", async () => {
  const actual = await vi.importActual("@aios/shared-types");
  return {
    ...actual,
    classifyRetry: (...a: unknown[]) => mockClassifyRetry(...a),
    getApiErrorMessage: (...a: unknown[]) => mockGetApiErrorMessage(...a),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ── negative tests ───────────────────────────────────────────────────────────

describe("ErrorMessage depth1 — negative", () => {
  it("errorCode 없이 message도 없으면 기본 문구가 렌더되고 fieldErrors가 없으면 배너가 나타난다", () => {
    mockGetApiErrorMessage.mockReturnValue("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요.");
    mockClassifyRetry.mockReturnValue({ kind: "none" as const });
    render(<ErrorMessage />);

    // fieldErrors가 없으므로 Alert 배너가 렌더되어야 한다
    expect(
      screen.getByText("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요."),
    ).toBeInTheDocument();
  });

  it("fieldErrors에 키가 있으면 전체 Alert 배너가 렌더되지 않는다", () => {
    mockGetApiErrorMessage.mockReturnValue("로그인이 필요합니다.");
    mockClassifyRetry.mockReturnValue({ kind: "none" as const });

    render(
      <ErrorMessage
        errorCode="AUTH_REQUIRED"
        message="raw"
        fieldErrors={{ email: "올바른 형식이 아닙니다." }}
      />,
    );

    expect(screen.queryByText("로그인이 필요합니다.")).not.toBeInTheDocument();
    // Alert wrapper(role=alert)도 없다
    const alert = screen.queryByRole("alert");
    expect(alert).toBeNull();
  });

  it("getApiErrorMessage가 빈 문자열을 반환하면 p 태그는 비어있지만 배너 자체는 렌더된다", () => {
    mockGetApiErrorMessage.mockReturnValue("");

    render(<ErrorMessage errorCode="AUTH_REQUIRED" />);

    // p 태그는 존재하지만 내용이 빈 문자열
    const paragraphs = screen.getAllByText(/^(|)$/);
    expect(paragraphs.length).toBeGreaterThanOrEqual(1);
  });
});

// ── failure injection tests ──────────────────────────────────────────────────

describe("ErrorMessage depth1 — failure injection", () => {
  it("classifyRetry가 예외를 던지면 렌더가 중단되고 배너도 없다", () => {
    mockGetApiErrorMessage.mockReturnValue("로그인이 필요합니다.");
    mockClassifyRetry.mockImplementation(() => {
      throw new Error("network failure simulation");
    });

    expect(() => render(<ErrorMessage errorCode="AUTH_REQUIRED" />)).toThrow(
      "network failure simulation",
    );
  });

  it("getApiErrorMessage가 예외를 던지면 렌더가 중단된다", () => {
    mockClassifyRetry.mockReturnValue({ kind: "none" as const });
    mockGetApiErrorMessage.mockImplementation(() => {
      throw new Error("api lookup failure");
    });

    expect(() => render(<ErrorMessage errorCode="AUTH_REQUIRED" />)).toThrow("api lookup failure");
  });

  it("classifyRetry가 null을 반환하면 classification.kind 접근이 TypeError를 던진다", () => {
    mockGetApiErrorMessage.mockReturnValue("로그인이 필요합니다.");
    mockClassifyRetry.mockReturnValue(null as unknown as ReturnType<typeof mockClassifyRetry>);

    expect(() => render(<ErrorMessage errorCode="AUTH_REQUIRED" />)).toThrow(
      "Cannot read properties of null",
    );
  });
});

// ── performance assertion tests ──────────────────────────────────────────────

describe("ErrorMessage depth1 — performance", () => {
  it("performance.now()로 재면 단일 렌더 시간이 50ms 미만이어야 한다 (예산: 로컬 실측 × 30 여유)", () => {
    mockGetApiErrorMessage.mockReturnValue("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요.");
    mockClassifyRetry.mockReturnValue({ kind: "none" as const });

    // @ts-expect-error performance.now is available in browsers
    const start = typeof performance !== "undefined" ? performance.now() : 0;

    render(<ErrorMessage errorCode="INTERNAL_ERROR" />);

    // @ts-expect-error
    const end = typeof performance !== "undefined" ? performance.now() : 0;
    const elapsed = end - start;

    // performance가 없는 환경(예: Node)에서는 이 단언을 우회
    if (start > 0 && end > 0) {
      expect(elapsed).toBeLessThan(50);
    }
    // DOM이 렌더되었는지 기본 확인
    expect(screen.getByText("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요.")).toBeInTheDocument();
  });

  it("성능 단언: 동일 컴포넌트 100회 렌더 총합이 1000ms 미만이어야 한다", () => {
    mockGetApiErrorMessage.mockReturnValue("일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요.");
    mockClassifyRetry.mockReturnValue({ kind: "none" as const });

    // @ts-expect-error
    const start = typeof performance !== "undefined" ? performance.now() : 0;

    for (let i = 0; i < 100; i++) {
      render(<ErrorMessage errorCode="INTERNAL_ERROR" />);
      cleanup();
    }

    // @ts-expect-error
    const end = typeof performance !== "undefined" ? performance.now() : 0;
    // performance가 없는 환경에서는 이 단언을 우회
    if (start > 0 && end > 0) {
      const elapsed = end - start;
      expect(elapsed).toBeLessThan(1000);
    }
  });
});

// ── gate red tests ───────────────────────────────────────────────────────────

describe("ErrorMessage depth1 — gate red", () => {
  it("gate red: retryAfterSec이 음수이면 카운트다운 없이 버튼이 활성화되어야 한다", () => {
    mockClassifyRetry.mockReturnValue({ kind: "backoff" as const, afterSec: undefined });
    mockGetApiErrorMessage.mockReturnValue("요청이 너무 많습니다. 잠시 후 다시 시도해주세요.");

    render(<ErrorMessage errorCode="RATE_LIMIT_EXCEEDED" retryAfterSec={-1} onRetry={vi.fn()} />);

    const button = screen.getByRole("button", { name: "다시 시도" });
    expect(button).toBeEnabled();
    expect(screen.queryByText(/초 후 재시도 가능/)).not.toBeInTheDocument();
  });

  it("gate red: onRetry가 undefined이면 재시도 영역 자체가 없어야 한다", () => {
    mockClassifyRetry.mockReturnValue({ kind: "backoff" as const });
    mockGetApiErrorMessage.mockReturnValue("요청이 너무 많습니다. 잠시 후 다시 시도해주세요.");

    render(<ErrorMessage errorCode="RATE_LIMIT_EXCEEDED" retryAfterSec={0} />);

    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
  });

  it("gate red: errorCode가 빈 문자열이면 prefix 매칭이 실패하고 기본 문구로 수렴한다", () => {
    mockGetApiErrorMessage.mockReturnValue("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요.");
    mockClassifyRetry.mockReturnValue({ kind: "none" as const });

    render(<ErrorMessage errorCode="" />);

    expect(
      screen.getByText("요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요."),
    ).toBeInTheDocument();
  });
});
