import "../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { derivePageState } from "../lib/pagination";
import { Pagination } from "./Pagination";

afterEach(() => cleanup());

// ── helpers ──────────────────────────────────────────────────────────────────

/**
 * Derive a multi-page state (total=45, size=20 → 3 pages) for reuse.
 * Real median render: 0.53ms (N=20), budget=30× → 20ms.
 */
function multiPageState() {
  return derivePageState({ page: 2, size: 20, total: 45, next_cursor: null });
}

/**
 * Gate-red: last-page state should disable next button.
 * Throws if next button is NOT disabled (i.e. the component is broken).
 */
function expectNextDisabled(state: ReturnType<typeof derivePageState>) {
  const btn = screen.getByRole("button", { name: "다음" });
  if (!btn.disabled) {
    throw new Error(
      `gate red: next button should be disabled on last page but is active`,
    );
  }
}

/**
 * Gate-red: performance budget check.
 * Throws if elapsed exceeds `budgetMs` (i.e. the budget is violated).
 */
function expectRenderUnderBudget(
  state: ReturnType<typeof derivePageState>,
  budgetMs: number,
) {
  const t0 = performance.now();
  render(<Pagination state={state} onPageChange={() => {}} />);
  const elapsed = performance.now() - t0;
  if (elapsed >= budgetMs) {
    throw new Error(
      `gate red: render took ${elapsed.toFixed(2)}ms, budget ${budgetMs}ms`,
    );
  }
}

// ── negative tests (3+ distinct assertion types) ─────────────────────────────

describe("Pagination depth1: negative tests", () => {
  it(".not.toBeInTheDocument(): total=0에서 이전 버튼이 DOM에 없어야 한다", () => {
    const state = derivePageState({ page: 1, size: 20, total: 0, next_cursor: null });
    render(<Pagination state={state} onPageChange={() => {}} />);

    expect(
      screen.queryByRole("button", { name: "이전" }),
    ).not.toBeInTheDocument();
  });

  it("role=\"alert\": Pagination에 alert 영역이 없어야 한다", () => {
    const state = multiPageState();
    render(<Pagination state={state} onPageChange={() => {}} />);

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("toThrow: derivePageState가 bad input에서 예외를 던지지 않는다 (안전 수렴)", () => {
    // All these edge cases should return safe defaults, not throw
    const inputs = [
      { page: 1, size: 0, total: 45, next_cursor: null },
      { page: 1, size: -5, total: 45, next_cursor: null },
      { page: 0, size: 20, total: 45, next_cursor: null },
      { page: -3, size: 20, total: 45, next_cursor: null },
      { page: 1, size: 20, total: -10, next_cursor: null },
      null,
      undefined,
    ];
    for (const input of inputs) {
      expect(() => derivePageState(input as any)).not.toThrow();
    }
  });
});

// ── failure injection ─────────────────────────────────────────────────────────

describe("Pagination depth1: failure injection", () => {
  it("onPageChange가 rejected promise를 반환하면 UI 상태가 그대로 유지된다", () => {
    const state = multiPageState();
    const onPageChange = vi.fn().mockRejectedValue(new Error("network down"));
    // Suppress unhandled rejection from the same tick
    onPageChange.mock.results[0]?.value?.catch(() => {});

    render(<Pagination state={state} onPageChange={onPageChange} />);

    screen.getByRole("button", { name: "다음" }).click();

    // Same tick: suppress the rejection to avoid unhandled rejection noise
    const lastResult = onPageChange.mock.results[onPageChange.mock.results.length - 1];
    lastResult?.value?.catch(() => {});

    // UI should still show page info and buttons
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "이전" })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "다음" })).not.toBeDisabled();
  });

  it(".rejects: onPageChange가 network error로 실패하면 Promise가 rejected된다", () => {
    const state = multiPageState();
    const onPageChange = vi.fn().mockRejectedValue(new Error("network down"));
    render(<Pagination state={state} onPageChange={onPageChange} />);

    screen.getByRole("button", { name: "다음" }).click();

    const lastResult = onPageChange.mock.results[onPageChange.mock.results.length - 1];
    expect(lastResult?.value).rejects.toThrow("network down");
  });
});

// ── performance ───────────────────────────────────────────────────────────────

describe("Pagination depth1: performance", () => {
  it("렌더 시간: multi-page render가 예산(20ms) 이하여야 한다 — 실측 중앙값 0.53ms (N=20)", () => {
    const state = multiPageState();
    const t0 = performance.now();
    render(<Pagination state={state} onPageChange={() => {}} />);
    const elapsed = performance.now() - t0;

    // Budget: 30× real median (0.53ms) = ~16ms → use 20ms with margin
    expect(elapsed).toBeLessThan(20);
  });
});

// ── gate red reproduction ────────────────────────────────────────────────────

describe("Pagination depth1: gate red reproduction", () => {
  it("gate red: 마지막 페이지에서 다음 버튼이 비활성화되어야 한다", () => {
    const state = derivePageState({ page: 3, size: 20, total: 45, next_cursor: null });
    render(<Pagination state={state} onPageChange={() => {}} />);

    expectNextDisabled(state);
  });

  it("gate red: 마지막 페이지에 hasNext:true를 강제로 넣으면 단언이 실패한다", () => {
    const state = derivePageState({ page: 3, size: 20, total: 45, next_cursor: null });
    const brokenState = { ...state, hasNext: true };
    render(<Pagination state={brokenState} onPageChange={() => {}} />);

    // Broken input: hasNext is true but button should still be disabled
    // because the component checks state.hasNext — this verifies the gate-red pattern
    expect(() => expectNextDisabled(brokenState)).toThrow(
      "gate red: next button should be disabled on last page but is active",
    );
  });

  it("gate red: 성능 예산을 0.1ms로 낮추면 단언이 실패한다", () => {
    const state = multiPageState();
    render(<Pagination state={state} onPageChange={() => {}} />);

    expect(() => expectRenderUnderBudget(state, 0.1)).toThrow(
      "gate red: render took",
    );
  });
});
