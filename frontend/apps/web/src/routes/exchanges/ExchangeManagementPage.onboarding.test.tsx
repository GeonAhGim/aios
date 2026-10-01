import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@aios/api-client";
import { ExchangeManagementPage } from "./ExchangeManagementPage";

// task-10642 (UX_JOURNEYS.md §6 J1 사용감 소견 F-4·F-5): ExchangeManagementPage.test.tsx가
// 이미 500줄 가까이(CLAUDE.md §6 #12) 쌓여있어, 이 리프가 추가하는 두 소견(F-4 온보딩
// 진행률 위젯·F-5 Secret/Passphrase 비운 이유 안내)의 증빙은 책임별로 분리한 이 파일에 둔다.
const mutateAsync = vi.fn();
let credentialsData: unknown[] = [];
let strategiesData: unknown[] = [];
let deploymentsData: { deployments: unknown[] } = { deployments: [] };

vi.mock("@aios/shared-hooks", () => ({
  useExchangeCredentials: () => ({ data: credentialsData, isLoading: false, isError: false, refetch: vi.fn() }),
  useRegisterExchangeCredential: () => ({ mutateAsync, isPending: false }),
  useRevokeExchangeCredential: () => ({ mutateAsync: vi.fn() }),
  useExchangeBalance: () => ({ data: undefined }),
  useExchangePositions: () => ({ data: undefined, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useMyStrategies: () => ({ data: strategiesData, isLoading: false, isError: false }),
  usePaperDeployments: () => ({ data: deploymentsData, isLoading: false, isError: false }),
  useMe: () => ({ data: { email: "a@example.com" } }),
  useLogout: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  mutateAsync.mockReset();
  credentialsData = [];
  strategiesData = [];
  deploymentsData = { deployments: [] };
});

function renderPage() {
  return render(
    <MemoryRouter>
      <ExchangeManagementPage />
    </MemoryRouter>,
  );
}

function passwordInputs(container: HTMLElement) {
  return [...container.querySelectorAll('input[type="password"]')] as HTMLInputElement[];
}

// F-4: 온보딩 체크리스트가 재사용하는 하위 화면(/exchanges)에 전체 진행률이 전혀
// 남지 않아, 완료 후 진행률을 보려면 /onboarding/first-run으로 직접 돌아가야 했다.
describe("ExchangeManagementPage 온보딩 진행률 위젯(F-4)", () => {
  it("온보딩이 미완료면 진행률 위젯과 체크리스트 링크를 보여준다", async () => {
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("온보딩 0/3단계 진행 중")).toBeInTheDocument(),
    );
    const link = screen.getByRole("link", { name: /온보딩 0\/3단계 진행 중/ });
    expect(link).toHaveAttribute("href", "/onboarding/first-run");
  });

  it("거래소 연결까지만 끝났으면 1/3단계로 보여준다", async () => {
    credentialsData = [{ id: 1, exchange: "bitget", isActive: true }];
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("온보딩 1/3단계 진행 중")).toBeInTheDocument(),
    );
  });

  it("negative: 온보딩 3단계가 모두 끝났으면 위젯을 숨긴다", async () => {
    credentialsData = [{ id: 1, exchange: "bitget", isActive: true }];
    strategiesData = [{ id: "s1" }];
    deploymentsData = { deployments: [{ id: "d1" }] };
    renderPage();

    await waitFor(() => expect(screen.queryByText(/온보딩 .+단계 진행 중/)).not.toBeInTheDocument());
  });
});

// F-5: 등록 실패 시 submitRegistration이 보안상 Secret/Passphrase를 지운다(유지해야
// 하는 동작) — 그 이유를 모르면 형식 오류 같은 단순 재시도 상황에서도 매번 처음부터
// 다시 타이핑해야 했다.
describe("ExchangeManagementPage Secret/Passphrase 비운 이유 안내(F-5)", () => {
  it("등록 실패 시 Secret/Passphrase가 비워진 이유를 안내한다", async () => {
    mutateAsync.mockRejectedValue(new ApiError(422, "raw", undefined, "EXCHANGE_FATAL"));
    const { container } = renderPage();

    const [apiKey, apiSecret, apiPassphrase] = passwordInputs(container);
    fireEvent.change(apiKey, { target: { value: "key-1" } });
    fireEvent.change(apiSecret, { target: { value: "secret-1" } });
    fireEvent.change(apiPassphrase, { target: { value: "pass-1" } });
    fireEvent.click(screen.getByRole("button", { name: "등록" }));

    await waitFor(() =>
      expect(
        screen.getByText("보안을 위해 Secret/Passphrase를 비웠습니다. 다시 입력해주세요."),
      ).toBeInTheDocument(),
    );
    expect(passwordInputs(container)[1].value).toBe("");
    expect(passwordInputs(container)[2].value).toBe("");
  });

  it("negative: 비운 이유 안내가 뜬 뒤 Secret을 다시 입력하면 안내가 사라진다", async () => {
    mutateAsync.mockRejectedValue(new ApiError(422, "raw", undefined, "EXCHANGE_FATAL"));
    const { container } = renderPage();

    const [apiKey, apiSecret, apiPassphrase] = passwordInputs(container);
    fireEvent.change(apiKey, { target: { value: "key-1" } });
    fireEvent.change(apiSecret, { target: { value: "secret-1" } });
    fireEvent.change(apiPassphrase, { target: { value: "pass-1" } });
    fireEvent.click(screen.getByRole("button", { name: "등록" }));

    await waitFor(() =>
      expect(
        screen.getByText("보안을 위해 Secret/Passphrase를 비웠습니다. 다시 입력해주세요."),
      ).toBeInTheDocument(),
    );

    fireEvent.change(passwordInputs(container)[1], { target: { value: "secret-2" } });

    expect(
      screen.queryByText("보안을 위해 Secret/Passphrase를 비웠습니다. 다시 입력해주세요."),
    ).not.toBeInTheDocument();
  });
});
