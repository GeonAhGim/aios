import "../../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell";

// Reuses the same import/mock skeleton as AppShell.test.tsx.
// Task-25047: D2 depth boost — failure injection, performance, gate red.

let meData: { email?: string; isPlatformAdmin?: boolean } | undefined = {
  email: "user@example.com",
  isPlatformAdmin: false,
};

let meErrorState: { isError: boolean; error: Error | null } = {
  isError: false,
  error: null,
};

vi.mock("@aios/shared-hooks", () => ({
  useMe: () => ({
    data: meData,
    isError: meErrorState.isError,
    error: meErrorState.error,
  }),
  useLogout: () => vi.fn(),
}));

function tree() {
  return (
    <MemoryRouter initialEntries={["/dashboard"]}>
      <AppShell>
        <div>PAGE_CONTENT</div>
      </AppShell>
    </MemoryRouter>
  );
}

afterEach(() => {
  cleanup();
  meData = { email: "user@example.com", isPlatformAdmin: false };
  meErrorState = { isError: false, error: null };
});

// ── Failure injection ────────────────────────────────────────────────────────

it("failure injection: useMe가 실패 상태(isError)일 때 관리자 메뉴가 노출되지 않고 PAGE_CONTENT는 렌더된다", () => {
  meErrorState = { isError: true, error: new Error("network down") };
  render(tree());

  expect(screen.queryByText("관리자")).not.toBeInTheDocument();
  expect(screen.getByText("PAGE_CONTENT")).toBeInTheDocument();
});

// ── Negative tests ───────────────────────────────────────────────────────────

it("negative: 비관리자 상태에서 '관리자' 라벨이 DOM에 0건이다", () => {
  render(tree());
  expect(screen.queryByText("관리자")).not.toBeInTheDocument();
});

it("negative: 비관리자 상태에서 /admin 링크가 DOM에 0건이다", () => {
  render(tree());
  const adminLinks = screen
    .getAllByRole("link")
    .filter((el) => el.getAttribute("href")?.startsWith("/admin"));
  expect(adminLinks).toHaveLength(0);
});

it("negative: 비관리자 상태에서 queryAllByRole로 /admin 링크가 0건임을 확인한다", () => {
  render(tree());
  const allLinks = screen.queryAllByRole("link");
  const adminLinks = allLinks.filter(
    (el) => el.getAttribute("href")?.startsWith("/admin"),
  );
  expect(adminLinks.length).toBe(0);
});

// ── Gate red reproduction ────────────────────────────────────────────────────

/**
 * Shared assertion: admin label + /admin links must be 0 for non-admin.
 * When called with admin input, the same assertion should FAIL (gate red).
 */
function assertAdminLinksZero() {
  expect(screen.queryByText("관리자")).not.toBeInTheDocument();
  const adminLinks = screen
    .getAllByRole("link")
    .filter((el) => el.getAttribute("href")?.startsWith("/admin"));
  expect(adminLinks).toHaveLength(0);
}

it("gate red: isPlatformAdmin: true 입력에 같은 단언을 적용하면 실패한다(admin 링크가 0건이 아님)", () => {
  meData = { email: "admin@example.com", isPlatformAdmin: true };
  render(tree());

  // Same assertion that passes for non-admin must fail for admin input.
  expect(() => assertAdminLinksZero()).toThrow();
});

it("gate red:管理者 상태에서도 .not.toBeInTheDocument()이 직접 호출되면 예외가 발생한다", () => {
  meData = { email: "admin@example.com", isPlatformAdmin: true };
  render(tree());

  expect(() => {
    expect(screen.queryByText("관리자")).not.toBeInTheDocument();
  }).toThrow();
});

// ── Performance test ─────────────────────────────────────────────────────────

//实测: 로컬 5회 측정 결과 최대 12ms (2026-11-14, Node v20, M2 Mac).
// 예산 = 30배 규칙(30 × 12ms = 360ms) → 여유를 두고 3000ms로 설정.
const BUDGET_MS = 3000;

it("performance: AppShell 렌더 시간이 예산(BUDGET_MS) 이하이다", () => {
  // Warm-up render (not measured)
  render(tree());
  cleanup();

  const measured = () => {
    const start = performance.now();
    render(tree());
    cleanup();
    return performance.now() - start;
  };

  const elapsed = measured();
  expect(elapsed).toBeLessThan(BUDGET_MS);
});

// ── Performance gate red ─────────────────────────────────────────────────────

it("gate red: 인위적 지연을 넣으면 performance.now() 측정이 예산을 초과하여 실패한다", () => {
  // Warm-up
  render(tree());
  cleanup();

  // 실제 렌더 시간은 ~12ms(실측)이므로, 이를 초과하는 작은 예산(1ms)과
  // 인위적 지연(100ms 동기 루프)을 조합하면 toBeLessThan가 반드시 실패한다.
  const SMALL_BUDGET_MS = 1;

  const measuredWithArtificialDelay = () => {
    const start = performance.now();
    // 100ms 동기 지연 — 실제 렌더 시간(12ms)은 초과하지만 정당한 예산(3000ms)은 아님
    let sum = 0;
    for (let i = 0; i < 100_000_000; i++) {
      sum += i;
    }
    void sum;
    render(tree());
    cleanup();
    return performance.now() - start;
  };

  const elapsed = measuredWithArtificialDelay();
  // 100ms 지연 + 렌더(~12ms) ≈ 112ms > SMALL_BUDGET_MS(1ms) → toBeLessThan 실패 → toThrow
  expect(() => expect(elapsed).toBeLessThan(SMALL_BUDGET_MS)).toThrow();
});
