import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { NotificationCenterPage } from "./NotificationCenterPage";

let historyResult: { data: unknown; isError: boolean; error: unknown; refetch: () => void } = {
  data: [],
  isError: false,
  error: null,
  refetch: () => {},
};

vi.mock("@aios/shared-hooks", () => ({
  useNotificationHistory: () => historyResult,
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => () => {},
}));

afterEach(() => {
  cleanup();
  historyResult = { data: [], isError: false, error: null, refetch: () => {} };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <NotificationCenterPage />
    </MemoryRouter>,
  );
}

const SAME_DAY_ENTRY = {
  eventType: "risk_mismatch",
  channel: "EMAIL",
  status: "SENT",
  createdAt: "2026-09-15T10:00:00.000Z",
};
const SAME_DAY_ENTRY_2 = {
  eventType: "verification_result",
  channel: "PUSH",
  status: "FAILED",
  createdAt: "2026-09-15T18:00:00.000Z",
};
const OTHER_DAY_ENTRY = {
  eventType: "marketplace_purchase",
  channel: "EMAIL",
  status: "SENT",
  createdAt: "2026-09-14T09:00:00.000Z",
};

describe("NotificationCenterPage: 이력·요약(UX-18 DoD)", () => {
  it("같은 날짜의 이력을 하나의 다이제스트 그룹으로 요약한다", async () => {
    historyResult = {
      data: [SAME_DAY_ENTRY, SAME_DAY_ENTRY_2, OTHER_DAY_ENTRY],
      isError: false,
      error: null,
      refetch: () => {},
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("2026-09-15")).toBeInTheDocument());
    expect(screen.getByText("2026-09-14")).toBeInTheDocument();
    expect(screen.getByText("risk_mismatch")).toBeInTheDocument();
    expect(screen.getByText("verification_result")).toBeInTheDocument();
  });

  it("채널 필터를 PUSH로 바꾸면 다른 채널 이력은 사라진다", async () => {
    historyResult = {
      data: [SAME_DAY_ENTRY, SAME_DAY_ENTRY_2],
      isError: false,
      error: null,
      refetch: () => {},
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("risk_mismatch")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("채널"), { target: { value: "PUSH" } });

    await waitFor(() => expect(screen.queryByText("risk_mismatch")).not.toBeInTheDocument());
    expect(screen.getByText("verification_result")).toBeInTheDocument();
  });

  it("정상 응답이지만 이력이 없으면 에러가 아닌 빈 상태 안내를 보여준다", async () => {
    historyResult = { data: [], isError: false, error: null, refetch: () => {} };
    renderPage();

    await waitFor(() => expect(screen.getByText("표시할 알림 이력이 없습니다.")).toBeInTheDocument());
  });

  // negative: 이력 조회가 403(AUTHZ_FORBIDDEN)으로 실패하면 raw 서버 메시지 대신
  // ForbiddenNotice의 매핑 문구를 보여준다(NotificationSettingsPage와 동일 관용).
  it("negative: 조회가 403으로 실패하면 ForbiddenNotice 매핑 문구를 보여준다", async () => {
    historyResult = {
      data: undefined,
      isError: true,
      error: new ApiError(403, "raw server detail", "trace-1", "AUTHZ_FORBIDDEN"),
      refetch: () => {},
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument());
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  // negative: 조회 실패가 조용히 삼켜지지 않고 배너로 드러나야 한다.
  it("negative: 조회 실패는 조용히 삼켜지지 않고 ErrorMessage 배너로 보여준다", async () => {
    historyResult = {
      data: undefined,
      isError: true,
      error: new Error("ECONNRESET"),
      refetch: () => {},
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("ECONNRESET")).toBeInTheDocument());
  });
});
