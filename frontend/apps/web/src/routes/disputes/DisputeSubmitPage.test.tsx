import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { DisputeSubmitPage } from "./DisputeSubmitPage";

const mutateAsync = vi.fn();

vi.mock("@aios/shared-hooks", () => ({
  useSubmitDispute: () => ({ mutateAsync, isPending: false }),
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
      <DisputeSubmitPage />
    </MemoryRouter>,
  );
}

function submitDisputeForm() {
  fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "42" } });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "상품이 설명과 다릅니다." } });
  fireEvent.click(screen.getByRole("button", { name: "분쟁 신고 제출" }));
}

// task-910 §3.3: 분쟁 신고 실패는 err.message를 직접 노출하지 않고 routeApiError로
// 판정해 BadRequestNotice/ForbiddenNotice/ErrorMessage 경로로만 보여준다.
describe("DisputeSubmitPage 신고 에러 표시", () => {
  it("negative: POLICY_*(403) 거부는 err.message 대신 ForbiddenNotice의 매핑 문구를 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(403, "raw server detail", "trace-1", "POLICY_LIVE_BLOCKED"),
    );
    renderPage();

    submitDisputeForm();

    await waitFor(() =>
      expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    mutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    renderPage();

    submitDisputeForm();

    await waitFor(() => expect(screen.getByText("분쟁 신고에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });

  it("VALIDATION_IDEMPOTENCY_KEY_REQUIRED(400)는 BadRequestNotice 경로로 새로고침 안내를 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(400, "raw detail", undefined, "VALIDATION_IDEMPOTENCY_KEY_REQUIRED"),
    );
    renderPage();

    submitDisputeForm();

    await waitFor(() =>
      expect(
        screen.getByText("요청이 올바르지 않습니다. 새로고침 후 다시 시도해주세요."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw detail")).not.toBeInTheDocument();
  });

  // task-954: classifyBadRequest가 VALIDATION_INVALID_FIELD를 "field"로 분류해
  // BadRequestNotice가 스스로 null을 렌더한다(task-364) — useFieldErrors가
  // details.fields[]를 읽어 입력 옆에 인라인 오류를 보여준다.
  it("VALIDATION_INVALID_FIELD(400): details.fields[]를 해당 입력 옆에 인라인 오류로 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(400, "요청 값이 올바르지 않습니다.", undefined, "VALIDATION_INVALID_FIELD", undefined, {
        fields: ["body.purchase_id", "body.reason"],
      }),
    );
    renderPage();

    submitDisputeForm();

    await waitFor(() =>
      expect(screen.getAllByText("요청 값이 올바르지 않습니다.")).toHaveLength(2),
    );
  });

  it("필드를 수정하면 clearField로 그 필드 오류만 사라지고 나머지는 유지된다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(400, "요청 값이 올바르지 않습니다.", undefined, "VALIDATION_INVALID_FIELD", undefined, {
        fields: ["body.purchase_id", "body.reason"],
      }),
    );
    renderPage();

    submitDisputeForm();
    await waitFor(() =>
      expect(screen.getAllByText("요청 값이 올바르지 않습니다.")).toHaveLength(2),
    );

    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "43" } });

    await waitFor(() =>
      expect(screen.getAllByText("요청 값이 올바르지 않습니다.")).toHaveLength(1),
    );
  });
});
