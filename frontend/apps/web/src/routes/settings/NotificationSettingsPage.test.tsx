import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@aios/api-client";
import { NotificationSettingsPage } from "./NotificationSettingsPage";

const updateMutate = vi.fn();
let preferencesResult: { data: unknown; isLoading: boolean; isError: boolean; error: unknown; refetch: () => void } = {
  data: { execution_alert: true, dispute_update: true },
  isLoading: false,
  isError: false,
  error: null,
  refetch: vi.fn(),
};
let updateResult: { mutate: typeof updateMutate; isError: boolean; error: unknown } = {
  mutate: updateMutate,
  isError: false,
  error: null,
};
let historyResult: { data: unknown; isError: boolean; error: unknown; refetch: () => void } = {
  data: [],
  isError: false,
  error: null,
  refetch: vi.fn(),
};

const pushEnable = vi.fn();
const pushDisable = vi.fn();
let pushResult: {
  status: "unsupported" | "idle" | "denied" | "subscribed" | "error";
  error: Error | null;
  deviceId: number | null;
  enable: typeof pushEnable;
  disable: typeof pushDisable;
  isEnabling: boolean;
  isDisabling: boolean;
} = {
  status: "idle",
  error: null,
  deviceId: null,
  enable: pushEnable,
  disable: pushDisable,
  isEnabling: false,
  isDisabling: false,
};

vi.mock("@aios/shared-hooks", () => ({
  useNotificationPreferences: () => preferencesResult,
  useUpdateNotificationPreferences: () => updateResult,
  useNotificationHistory: () => historyResult,
  useMe: () => ({ data: { email: "a@example.com", isPlatformAdmin: false } }),
  useLogout: () => vi.fn(),
}));

vi.mock("../../pwa/usePushNotifications", () => ({
  usePushNotifications: () => pushResult,
}));

afterEach(() => {
  cleanup();
  updateMutate.mockReset();
  pushEnable.mockReset();
  pushDisable.mockReset();
  preferencesResult = {
    data: { execution_alert: true, dispute_update: true },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
  updateResult = { mutate: updateMutate, isError: false, error: null };
  historyResult = { data: [], isError: false, error: null, refetch: vi.fn() };
  pushResult = {
    status: "idle",
    error: null,
    deviceId: null,
    enable: pushEnable,
    disable: pushDisable,
    isEnabling: false,
    isDisabling: false,
  };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <NotificationSettingsPage />
    </MemoryRouter>,
  );
}

// task-1155 spec §3.3: 조회·변경 실패는 err.message를 직접 노출하지 않고
// routeApiError로 판정해 ForbiddenNotice/ErrorMessage 경로로만 보여준다 — 이전에는
// preferences/history 조회 실패, update.mutate 실패 모두 화면에 드러나지 않았다.
describe("NotificationSettingsPage 조회·변경 에러 표시", () => {
  it("정상 응답이면 수신 설정을 렌더링한다", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("execution_alert")).toBeInTheDocument());
  });

  it("negative: 수신 설정 조회가 403(AUTHZ_FORBIDDEN)으로 실패하면 ForbiddenNotice의 매핑 문구를 보여준다", async () => {
    preferencesResult = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(403, "raw server detail", "trace-1", "AUTHZ_FORBIDDEN"),
      refetch: vi.fn(),
    };
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("이 작업을 수행할 권한이 없습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("raw server detail")).not.toBeInTheDocument();
  });

  it("negative: 알림 이력 조회 실패는 조용히 삼켜지지 않고 ErrorMessage 배너로 보여준다", async () => {
    historyResult = {
      data: undefined,
      isError: true,
      error: new Error("ECONNRESET"),
      refetch: vi.fn(),
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("ECONNRESET")).toBeInTheDocument());
  });

  it("negative: 수신 설정 변경(체크박스 토글)이 실패하면 조용히 삼켜지지 않고 에러 배너를 보여준다", async () => {
    updateResult = {
      mutate: updateMutate,
      isError: true,
      error: new ApiError(500, "일시적인 오류입니다.", "trace-2"),
    };
    renderPage();

    await waitFor(() => expect(screen.getByText("execution_alert")).toBeInTheDocument());
    fireEvent.click(screen.getAllByRole("checkbox")[0]);

    expect(updateMutate).toHaveBeenCalledWith({ execution_alert: false });
    await waitFor(() => expect(screen.getByText("일시적인 오류입니다.")).toBeInTheDocument());
  });

  it("정상 응답이지만 알림 이력이 없으면 에러가 아닌 빈 상태 안내를 보여준다", async () => {
    historyResult = { data: [], isError: false, error: null, refetch: vi.fn() };
    renderPage();

    await waitFor(() => expect(screen.getByText("알림 이력이 없습니다.")).toBeInTheDocument());
    expect(screen.getByText("execution_alert")).toBeInTheDocument();
  });
});

// UX-17: 브라우저 지원 여부·권한 흐름을 usePushNotifications의 status에 그대로 반영하는지 확인.
describe("NotificationSettingsPage 푸시 알림 카드", () => {
  it("unsupported면 지원하지 않는다는 안내만 보이고 켜기 버튼은 없다", async () => {
    pushResult = { ...pushResult, status: "unsupported" };
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("이 브라우저/기기는 푸시 알림을 지원하지 않습니다.")).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "알림 켜기" })).not.toBeInTheDocument();
  });

  it("idle 상태에서 알림 켜기를 누르면 enable을 호출한다", async () => {
    renderPage();

    await waitFor(() => expect(screen.getByRole("button", { name: "알림 켜기" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "알림 켜기" }));

    expect(pushEnable).toHaveBeenCalledTimes(1);
  });

  it("subscribed 상태면 해지 버튼을 보여주고 누르면 disable을 호출한다", async () => {
    pushResult = { ...pushResult, status: "subscribed", deviceId: 3 };
    renderPage();

    await waitFor(() => expect(screen.getByRole("button", { name: "구독 해지" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "구독 해지" }));

    expect(pushDisable).toHaveBeenCalledTimes(1);
  });

  // negative: 권한 거부는 재시도 가능한 에러(error)와 구분되는 안내(브라우저 설정
  // 변경 필요)를 보여줘야 한다 — 같은 배너로 뭉치면 사용자가 재시도 버튼만 누르고
  // 계속 실패하는 루프에 빠진다.
  it("negative: denied 상태면 브라우저 설정 안내 문구를 보여준다", async () => {
    pushResult = { ...pushResult, status: "denied" };
    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/브라우저 알림 권한이 거부되었습니다/)).toBeInTheDocument(),
    );
  });

  it("negative: error 상태면 에러 메시지를 배너로 보여준다", async () => {
    pushResult = { ...pushResult, status: "error", error: new Error("구독에 실패했습니다") };
    renderPage();

    await waitFor(() => expect(screen.getByText("구독에 실패했습니다")).toBeInTheDocument());
  });
});
