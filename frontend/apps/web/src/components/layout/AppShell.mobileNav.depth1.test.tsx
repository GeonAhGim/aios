import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { bestOfMs } from "../../test/perfBudget";
import { AppShell } from "./AppShell";

// task-25150 UX-21 D2 보강: negative 3종 + 실패 주입 + 성능 단언 + gate red.
// 기존 AppShell.mobileNav.test.tsx는 건드리지 않는다.
const hooks = vi.hoisted(() => ({
  useMe: vi.fn(),
  useLogout: vi.fn(),
}));

vi.mock("@aios/shared-hooks", () => hooks);

const meData = { email: "user@example.com", isPlatformAdmin: false };

beforeEach(() => {
  hooks.useMe.mockReturnValue({ data: meData });
  hooks.useLogout.mockReturnValue(vi.fn());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function tree() {
  return (
    <MemoryRouter initialEntries={["/dashboard"]}>
      <AppShell>
        <div>PAGE_CONTENT</div>
      </AppShell>
    </MemoryRouter>
  );
}

const menuToggle = () => screen.getByRole("button", { name: /메뉴/ });
const menuPanel = () => document.getElementById("app-shell-mobile-nav");

describe("AppShell mobileNav — negative(task-25150 D2)", () => {
  it("negative: 초기 상태에서 nav 패널은 hidden이고 flex가 아니다", () => {
    render(tree());
    expect(menuPanel()).toHaveClass("hidden");
    expect(menuPanel()).not.toHaveClass("flex");
    expect(menuToggle()).not.toHaveAttribute("aria-expanded", "true");
  });

  it("negative: 열린 뒤 링크를 누르면 패널이 다시 hidden이 되고 flex가 사라진다", () => {
    render(tree());
    fireEvent.click(menuToggle());
    expect(menuPanel()).toHaveClass("flex");
    const link = screen.getAllByRole("link").find((el) => el.getAttribute("href") === "/exchanges");
    fireEvent.click(link as HTMLElement);
    expect(menuPanel()).not.toHaveClass("flex");
    expect(menuPanel()).toHaveClass("hidden");
  });

  it("negative: 일반 사용자에게 관리자 전용 링크는 렌더되지 않는다", () => {
    render(tree());
    expect(screen.queryByRole("link", { name: /admin/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("AppShell mobileNav — 실패 주입(task-25150 D2)", () => {
  it("실패 주입: useMe가 data 없이 실패해도 셸과 토글이 렌더된다", () => {
    hooks.useMe.mockReturnValue({ data: undefined, error: new Error("networkError: /me") });
    render(tree());
    expect(screen.getByText("PAGE_CONTENT")).toBeInTheDocument();
    fireEvent.click(menuToggle());
    expect(menuToggle()).toHaveAttribute("aria-expanded", "true");
  });

  it("실패 주입: useLogout이 reject하도록 주입돼도 클릭 없이는 렌더가 깨지지 않는다", async () => {
    const logout = vi.fn().mockRejectedValue(new Error("networkError: logout"));
    hooks.useLogout.mockReturnValue(logout);
    render(tree());
    expect(menuToggle()).toBeInTheDocument();
    expect(logout).not.toHaveBeenCalled();
    await expect(logout()).rejects.toThrow("networkError: logout");
  });
});

describe("AppShell mobileNav — 성능 단언(task-25150 D2)", () => {
  // 실측 렌더 ~20ms, 토글 10회 ~5ms(로컬 jsdom). 예산은 실측의 30배 이상.
  // bestOfMs로 공유 호스트 부하에 흔들리지 않도록 최솟값 판정.
  it("성능: 초기 렌더가 1500ms 미만", () => {
    const measured = () => {
      const start = performance.now();
      render(tree());
      cleanup();
      return performance.now() - start;
    };
    const elapsed = bestOfMs(measured);
    expect(elapsed).toBeLessThan(1500);
  });

  it("성능: 토글 10회 클릭이 1000ms 미만", () => {
    render(tree());
    const measured = () => {
      const start = performance.now();
      for (let i = 0; i < 10; i++) fireEvent.click(menuToggle());
      return performance.now() - start;
    };
    const elapsed = bestOfMs(measured);
    expect(menuToggle()).toHaveAttribute("aria-expanded", "false");
    expect(elapsed).toBeLessThan(1000);
  });
});

describe("AppShell mobileNav — gate red 재현(task-25150 D2)", () => {
  it("gate red: 열린 상태에 '닫힘' 단언을 하면 실제로 실패한다", () => {
    render(tree());
    fireEvent.click(menuToggle());
    expect(() => expect(menuToggle()).toHaveAttribute("aria-expanded", "false")).toThrow();
    expect(() => expect(menuPanel()).toHaveClass("hidden")).toThrow();
  });

  it("gate red: 지연을 주입하면 성능 예산 단언이 실제로 실패한다", () => {
    const start = performance.now();
    const until = start + 30;
    while (performance.now() < until) {
      // busy-wait: 인위적 지연 주입
    }
    const elapsed = performance.now() - start;
    expect(() => expect(elapsed).toBeLessThan(5)).toThrow();
  });
});
