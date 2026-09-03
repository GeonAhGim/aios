import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { SellStrategyPage } from "./SellStrategyPage";

const mutateAsync = vi.fn();

vi.mock("@aios/shared-hooks", () => ({
  useMyStrategies: () => ({
    data: [{ strategyId: "strat-1", version: "1.0.0", lifecycleStatus: "ACTIVE" }],
    isLoading: false,
  }),
  useCreateListing: () => ({ mutateAsync, isPending: false }),
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  mutateAsync.mockReset();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <SellStrategyPage />
    </MemoryRouter>,
  );
}

// task-929 §3.3: 리스팅 생성 실패는 err.message를 직접 노출하지 않고 routeApiError로
// 판정해 BadRequestNotice/ForbiddenNotice/ErrorMessage 경로로만 보여준다.
describe("SellStrategyPage 리스팅 생성 에러 표시", () => {
  it("negative: 미지 error_code(VALIDATION_ 접두) 400은 err.message 대신 BadRequestNotice의 접두 폴백 문구를 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(400, "raw server detail", undefined, "VALIDATION_SOME_NEW_FIELD"),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "리스팅 등록 (초안)" }));

    await waitFor(() => expect(screen.getByText("입력값을 확인해주세요.")).toBeInTheDocument());
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    mutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "리스팅 등록 (초안)" }));

    await waitFor(() => expect(screen.getByText("리스팅 생성에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });

  // task-954: classifyBadRequest가 VALIDATION_INVALID_FIELD를 "field"로 분류해
  // BadRequestNotice가 스스로 null을 렌더한다(task-364) — useFieldErrors가
  // details.fields[]를 읽어 입력 옆에 인라인 오류를 보여준다.
  it("VALIDATION_INVALID_FIELD(400): details.fields[]를 해당 입력 옆에 인라인 오류로 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(400, "요청 값이 올바르지 않습니다.", undefined, "VALIDATION_INVALID_FIELD", undefined, {
        fields: ["body.strategy_id", "body.price"],
      }),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "리스팅 등록 (초안)" }));

    await waitFor(() =>
      expect(screen.getAllByText("요청 값이 올바르지 않습니다.")).toHaveLength(2),
    );
  });

  it("필드를 수정하면 clearField로 그 필드 오류만 사라지고 나머지는 유지된다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(400, "요청 값이 올바르지 않습니다.", undefined, "VALIDATION_INVALID_FIELD", undefined, {
        fields: ["body.strategy_id", "body.price"],
      }),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "리스팅 등록 (초안)" }));
    await waitFor(() =>
      expect(screen.getAllByText("요청 값이 올바르지 않습니다.")).toHaveLength(2),
    );

    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "20.00" } });

    await waitFor(() =>
      expect(screen.getAllByText("요청 값이 올바르지 않습니다.")).toHaveLength(1),
    );
  });
});
