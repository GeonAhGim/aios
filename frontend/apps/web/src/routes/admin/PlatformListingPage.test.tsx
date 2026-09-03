import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { PlatformListingPage } from "./PlatformListingPage";

const mutateAsync = vi.fn();

vi.mock("@aios/shared-hooks", () => ({
  useCreatePlatformListing: () => ({ mutateAsync, isPending: false }),
  useMe: () => ({ data: { email: "admin@example.com", isPlatformAdmin: true } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  mutateAsync.mockReset();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <PlatformListingPage />
    </MemoryRouter>,
  );
}

// Field가 htmlFor/id 연결을 하지 않으므로(ui-web/Field.tsx) 라벨 텍스트 대신
// role + DOM 순서로 입력을 찾는다 — 전략 ID/버전이 나란한 텍스트 입력 두 개다.
function strategyIdInput(container: HTMLElement): HTMLInputElement {
  return container.querySelectorAll('input[type="text"]')[0] as HTMLInputElement;
}

function submitPlatformListingForm(container: HTMLElement) {
  fireEvent.change(strategyIdInput(container), { target: { value: "strat-1" } });
  fireEvent.click(screen.getByRole("button", { name: "등록" }));
}

// task-911 §3.3: 등록 실패는 err.message를 직접 노출하지 않고 routeApiError로
// 판정해 ForbiddenNotice/ErrorMessage 경로로만 보여준다.
describe("PlatformListingPage 등록 에러 표시", () => {
  it("negative: POLICY_*(403) 거부는 err.message 대신 ForbiddenNotice의 매핑 문구를 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(403, "raw server detail", "trace-1", "POLICY_LIVE_BLOCKED"),
    );
    const { container } = renderPage();

    submitPlatformListingForm(container);

    await waitFor(() =>
      expect(screen.getByText("실거래 모드에서는 허용되지 않는 작업입니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  // task-1072 §3.3: POLICY_*/RISK_* 403의 details.reason_codes 봉투는 ForbiddenNotice가
  // 위임하는 DenialReasons가 사유별 문장 목록으로 보여줘야 한다 — 지금까지 이 화면
  // 테스트는 errorCode 하나만 봤고 details.reason_codes가 실제로 파싱되는지는
  // 검증하지 않았다.
  it("negative: POLICY_*(403) + details.reason_codes 봉투: DenialReasons가 사유 목록을 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(403, "실거래 모드에서는 허용되지 않는 작업입니다.", "trace-1", "POLICY_LIVE_BLOCKED", undefined, {
        reason_codes: ["POLICY_LIVE_BLOCKED", "RISK_MAX_DRAWDOWN_EXCEEDED"],
      }),
    );
    const { container } = renderPage();

    submitPlatformListingForm(container);

    await waitFor(() => expect(screen.getByRole("list")).toBeInTheDocument());
    const denialList = screen.getByRole("list");
    expect(denialList).toHaveTextContent("실거래 모드에서는 허용되지 않는 작업입니다.");
    expect(denialList).toHaveTextContent("최대 손실 한도를 초과하여 거부되었습니다.");
  });

  it("negative: ApiError가 아닌 실패는 raw message 대신 안전한 fallback 문구를 보여준다", async () => {
    mutateAsync.mockRejectedValue(new Error("ECONNRESET"));
    const { container } = renderPage();

    submitPlatformListingForm(container);

    await waitFor(() => expect(screen.getByText("리스팅 등록에 실패했습니다.")).toBeInTheDocument());
    expect(screen.queryByText("ECONNRESET")).not.toBeInTheDocument();
  });

  // task-954: 이 라우트는 classifyBadRequest/BadRequestNotice를 쓰지 않으므로
  // VALIDATION_INVALID_FIELD(400)는 기존에도 ErrorMessage 배너로 흘렀다 —
  // fieldErrors가 비어있지 않으면 그 배너 대신 useFieldErrors가 details.fields[]를
  // 읽어 입력 옆에 인라인 오류를 보여준다.
  it("VALIDATION_INVALID_FIELD(400): details.fields[]를 해당 입력 옆에 인라인 오류로 보여준다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(400, "요청 값이 올바르지 않습니다.", undefined, "VALIDATION_INVALID_FIELD", undefined, {
        fields: ["body.strategy_id", "body.strategy_version"],
      }),
    );
    const { container } = renderPage();

    submitPlatformListingForm(container);

    await waitFor(() =>
      expect(screen.getAllByText("요청 값이 올바르지 않습니다.")).toHaveLength(2),
    );
  });

  it("필드를 수정하면 clearField로 그 필드 오류만 사라지고 나머지는 유지된다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(400, "요청 값이 올바르지 않습니다.", undefined, "VALIDATION_INVALID_FIELD", undefined, {
        fields: ["body.strategy_id", "body.strategy_version"],
      }),
    );
    const { container } = renderPage();

    submitPlatformListingForm(container);
    await waitFor(() =>
      expect(screen.getAllByText("요청 값이 올바르지 않습니다.")).toHaveLength(2),
    );

    fireEvent.change(strategyIdInput(container), { target: { value: "strat-2" } });

    await waitFor(() =>
      expect(screen.getAllByText("요청 값이 올바르지 않습니다.")).toHaveLength(1),
    );
  });

  it("VALIDATION_INVALID_FIELD(400)에서 details.fields가 비어있으면 ErrorMessage 배너로 폴백한다", async () => {
    mutateAsync.mockRejectedValue(
      new ApiError(400, "raw detail", undefined, "VALIDATION_INVALID_FIELD"),
    );
    const { container } = renderPage();

    submitPlatformListingForm(container);

    await waitFor(() => expect(screen.getByText("입력값을 확인해주세요.")).toBeInTheDocument());
    expect(screen.queryByText("raw detail")).not.toBeInTheDocument();
  });
});
