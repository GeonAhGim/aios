import "../../i18n";
// UX-3 (task-2687): apps/web is the only project in this workspace whose vitest
// project runs jsdom (see vitest.config.ts header comment), so the real browser-path
// singleton (packages/ui-web/src/theme.ts's module-scope `themeStore`, using real
// `window`/`document`/`localStorage`) can only be exercised end to end from here --
// packages/ui-web/src/theme.test.ts covers the DI-injectable logic in isolation but
// never touches the real DOM. ADR-2026-09-09-C D2: negative >=3, failure injection 1,
// performance assertion 1, gate-red repro 1.
import "@testing-library/jest-dom/vitest";
import { ThemeToggle, themeStore } from "@aios/ui-web";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { perfBudgetMs } from "../../test/perfBudget";

function resetToDark() {
  try {
    window.localStorage.removeItem("aios-theme");
  } catch {
    // ignore
  }
  document.documentElement.removeAttribute("data-theme");
  if (themeStore.getSnapshot() !== "dark") {
    themeStore.setTheme("dark");
  } else {
    // getSnapshot() may have just re-hydrated and set the attribute back; the
    // removeAttribute above must win as the actual pre-test state either way.
    document.documentElement.setAttribute("data-theme", "dark");
  }
}

beforeEach(() => {
  resetToDark();
});

afterEach(() => {
  cleanup();
  resetToDark();
});

describe("ThemeToggle (packages/ui-web) wired into a real DOM", () => {
  it("renders with an accessible name and defaults to dark", () => {
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: "Toggle color theme" })).toBeInTheDocument();
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("clicking flips document.documentElement's data-theme and persists the choice", () => {
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("button", { name: "Toggle color theme" }));

    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(window.localStorage.getItem("aios-theme")).toBe("light");
  });

  it("[negative] clicking twice returns to dark and clears back to the dark persisted value", () => {
    render(<ThemeToggle />);
    const button = screen.getByRole("button", { name: "Toggle color theme" });
    fireEvent.click(button);
    fireEvent.click(button);

    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(window.localStorage.getItem("aios-theme")).toBe("dark");
  });

  it("[negative] a custom aria-label overrides the default without changing toggle behavior", () => {
    render(<ThemeToggle aria-label="테마 전환" />);
    const button = screen.getByRole("button", { name: "테마 전환" });
    fireEvent.click(button);
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it(
    "[gate-red repro] two independently mounted ThemeToggle instances stay in sync -- " +
      "a naive per-component useState implementation (instead of the shared " +
      "useSyncExternalStore-backed themeStore) would leave the second instance stuck",
    () => {
      render(
        <div>
          <ThemeToggle aria-label="toggle-a" />
          <ThemeToggle aria-label="toggle-b" />
        </div>,
      );
      const a = screen.getByRole("button", { name: "toggle-a" });
      const b = screen.getByRole("button", { name: "toggle-b" });

      expect(a.dataset.themeToggle).toBe("dark");
      expect(b.dataset.themeToggle).toBe("dark");

      fireEvent.click(a);

      // Both instances must reflect the new theme -- if ThemeToggle held its own
      // local state instead of reading the shared store, `b` would still say "dark"
      // here even though the actual page (document.documentElement) already
      // switched, i.e. the icon would visibly lie about the current theme.
      expect(a.dataset.themeToggle).toBe("light");
      expect(b.dataset.themeToggle).toBe("light");
      expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    },
  );

  it("[failure injection] localStorage.setItem throwing (private-mode quota) does not block the visible theme switch", () => {
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    try {
      render(<ThemeToggle />);
      fireEvent.click(screen.getByRole("button", { name: "Toggle color theme" }));

      // Persistence failed silently, but the in-session switch must still have
      // happened -- users should not be stuck on the wrong theme just because
      // their browser refused the storage write.
      expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    } finally {
      setItemSpy.mockRestore();
    }
  });

  it("[perf budget] 50 sequential toggle clicks stay within budget", () => {
    render(<ThemeToggle />);
    const button = screen.getByRole("button", { name: "Toggle color theme" });

    const started = performance.now();
    for (let i = 0; i < 50; i += 1) {
      fireEvent.click(button);
    }
    const elapsed = performance.now() - started;

    expect(elapsed).toBeLessThan(perfBudgetMs(500));
  });
});
