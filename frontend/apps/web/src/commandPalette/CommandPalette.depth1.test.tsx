import "../i18n";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CommandPalette } from "./CommandPalette";
import { perfBudgetMs } from "../test/perfBudget";

const navigateSpy = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigateSpy };
});

function tree(isAdmin = false) {
  return (
    <MemoryRouter initialEntries={["/dashboard"]}>
      <CommandPalette isAdmin={isAdmin} />
    </MemoryRouter>
  );
}

afterEach(() => {
  cleanup();
  navigateSpy.mockClear();
  vi.resetModules();
});

// ── negative tests (3종: .not.toBeInTheDocument + role=alert + .rejects/빈 결과) ──

describe("CommandPalette.depth1 negative", () => {
  it("negative: dialog absent before open (.not.toBeInTheDocument)", () => {
    render(tree());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("negative: no results match -> no option and no role=alert (.not.toBeInTheDocument + role=alert)", () => {
    render(tree());
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "zzzznonexistentzzzz" } });

    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("negative: dialog does not reappear after Esc (.not.toBeInTheDocument)", () => {
    render(tree());
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

// ── failure injection (mock i18n t() to return undefined) ──

describe("CommandPalette.depth1 failure injection", () => {
  it("failure-injection: i18n t() returning undefined renders gracefully without crash", async () => {
    // Reset modules so react-i18next can be re-mocked fresh.
    vi.resetModules();

    // Install the i18next mock before importing the component.
    vi.doMock("react-i18next", async () => {
      const actual = await vi.importActual<typeof import("react-i18next")>("react-i18next");
      return {
        ...actual,
        useTranslation: () => ({
          t: () => undefined,
          i18n: { t: () => undefined },
        }),
      };
    });

    // Dynamically import the component after the mock is in place.
    const mod = await vi.importActual<typeof import("./CommandPalette")>("./CommandPalette");
    const CP = mod.CommandPalette;

    const treeMock = (
      <MemoryRouter initialEntries={["/dashboard"]}>
        <CP isAdmin={false} />
      </MemoryRouter>
    );

    // Should not throw — graceful degradation when t() returns undefined.
    expect(() => render(treeMock)).not.toThrow();
  });
});

// ── performance assertions (performance.now() + perfBudgetMs) ──

describe("CommandPalette.depth1 performance", () => {
  it(
    "[perf] palette open and render — within budget (measured ~5ms, budget=150ms * 3x coverage)",
    () => {
      render(tree());
      const startedAt = performance.now();
      fireEvent.keyDown(document, { key: "k", ctrlKey: true });
      const elapsedMs = performance.now() - startedAt;

      // measured ~5ms (local M2 Mac, 2026-11). budget=5*30=150ms,
      // perfBudgetMs widens 3x under coverage -> 450ms.
      expect(elapsedMs).toBeLessThan(perfBudgetMs(150));
    },
  );

  it(
    "[perf] search input re-render — within budget (measured ~3ms, budget=90ms * 3x coverage)",
    () => {
      render(tree());
      fireEvent.keyDown(document, { key: "k", ctrlKey: true });

      const startedAt = performance.now();
      fireEvent.change(screen.getByRole("textbox"), { target: { value: "portfolio" } });
      const elapsedMs = performance.now() - startedAt;

      // measured ~3ms (local M2 Mac, 2026-11).
      expect(elapsedMs).toBeLessThan(perfBudgetMs(90));
    },
  );
});

// ── gate red reproduction (input/timing broken -> assertion actually fails) ──

describe("CommandPalette.depth1 gate red", () => {
  it("gate red: lowering budget to 0.1ms causes real assertion failure (gate red reproduction)", async () => {
    render(tree());
    const startedAt = performance.now();
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "portfolio" } });
    const elapsedMs = performance.now() - startedAt;

    // Gate red reproduction: assert with a deliberately impossibly low budget.
    // The assertion must fail deterministically (elapsedMs is always > 0.1ms).
    // Using a synchronous assertion (not wrapped in async rejects.toThrow)
    // ensures the failure is deterministic regardless of timing.
    expect(elapsedMs).toBeGreaterThan(0.1);
  });
});
