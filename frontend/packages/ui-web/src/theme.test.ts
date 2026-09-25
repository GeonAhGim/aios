// UX-3 (task-2687) theme store tests. ui-web's default vitest environment is "node"
// (see vitest.config.ts header comment) -- there is no real `document`/`window`/
// `localStorage` here, so createThemeStore takes storage/root via dependency
// injection specifically so this file can exercise every branch (persist, corrupt
// storage, no-op no-listener notification, DOM attribute application) with plain
// fakes instead of needing jsdom. ADR-2026-09-09-C D2: negative >=3, failure
// injection 1, perf assertion 1, gate-red repro 1.
import { describe, expect, it, vi } from "vitest";
import { applyThemeAttribute, createThemeStore, isThemeMode, resolveInitialTheme } from "./theme";

function fakeStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => (data.has(key) ? data.get(key)! : null),
    setItem: (key: string, value: string) => data.set(key, value),
    _data: data,
  };
}

function fakeRoot() {
  const attrs = new Map<string, string>();
  return { setAttribute: (name: string, value: string) => attrs.set(name, value), _attrs: attrs };
}

describe("isThemeMode / resolveInitialTheme (pure)", () => {
  it("accepts exactly 'light' and 'dark'", () => {
    expect(isThemeMode("light")).toBe(true);
    expect(isThemeMode("dark")).toBe(true);
  });

  it("[negative] rejects any other string, including near-misses and case variants", () => {
    expect(isThemeMode("Dark")).toBe(false);
    expect(isThemeMode("system")).toBe(false);
    expect(isThemeMode("")).toBe(false);
  });

  it("[negative] rejects non-string values without throwing", () => {
    expect(isThemeMode(null)).toBe(false);
    expect(isThemeMode(undefined)).toBe(false);
    expect(isThemeMode(42)).toBe(false);
  });

  it("resolveInitialTheme falls back to dark for null/corrupt values, and passes through valid ones", () => {
    expect(resolveInitialTheme(null)).toBe("dark");
    expect(resolveInitialTheme("not-a-theme")).toBe("dark");
    expect(resolveInitialTheme("light")).toBe("light");
    expect(resolveInitialTheme("dark")).toBe("dark");
  });
});

describe("applyThemeAttribute", () => {
  it("sets data-theme on the given root", () => {
    const root = fakeRoot();
    applyThemeAttribute(root, "light");
    expect(root._attrs.get("data-theme")).toBe("light");
  });
});

describe("createThemeStore", () => {
  it("hydrates from storage on first getSnapshot() call and applies the attribute to root", () => {
    const storage = fakeStorage({ "aios-theme": "light" });
    const root = fakeRoot();
    const store = createThemeStore({ storage, root });

    expect(store.getSnapshot()).toBe("light");
    expect(root._attrs.get("data-theme")).toBe("light");
  });

  it("defaults to dark when storage has no stored theme", () => {
    const store = createThemeStore({ storage: fakeStorage(), root: fakeRoot() });
    expect(store.getSnapshot()).toBe("dark");
  });

  it("[negative] defaults to dark when storage holds a corrupt/unrecognized value", () => {
    const store = createThemeStore({ storage: fakeStorage({ "aios-theme": "sepia" }), root: fakeRoot() });
    expect(store.getSnapshot()).toBe("dark");
  });

  it("setTheme persists to storage, updates root, and notifies subscribers exactly once", () => {
    const storage = fakeStorage();
    const root = fakeRoot();
    const store = createThemeStore({ storage, root });
    const listener = vi.fn();
    store.subscribe(listener);

    store.setTheme("light");

    expect(store.getSnapshot()).toBe("light");
    expect(root._attrs.get("data-theme")).toBe("light");
    expect(storage.getItem("aios-theme")).toBe("light");
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("[negative] setTheme with the same value is a no-op: no re-notify, no redundant storage write", () => {
    const storage = fakeStorage({ "aios-theme": "dark" });
    const setItemSpy = vi.spyOn(storage, "setItem");
    const store = createThemeStore({ storage, root: fakeRoot() });
    store.getSnapshot(); // hydrate
    const listener = vi.fn();
    store.subscribe(listener);

    store.setTheme("dark");

    expect(listener).not.toHaveBeenCalled();
    expect(setItemSpy).not.toHaveBeenCalled();
  });

  it("unsubscribe stops further notifications", () => {
    const store = createThemeStore({ storage: fakeStorage(), root: fakeRoot() });
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    unsubscribe();

    store.setTheme("light");

    expect(listener).not.toHaveBeenCalled();
  });

  it("[gate-red repro] a store built without DI-aware guards would throw when storage/root are absent; this one degrades to in-memory dark instead", () => {
    // Simulates the ui-web node test environment itself: no window/document. The
    // naive implementation this replaced called `window.localStorage`/
    // `document.documentElement` unconditionally inside hydrate() and setTheme()
    // -- that throws ReferenceError here (red). Passing storage/root as `null`
    // reproduces that exact absence and proves the guarded version stays green.
    const store = createThemeStore({ storage: null, root: null });
    expect(() => store.getSnapshot()).not.toThrow();
    expect(store.getSnapshot()).toBe("dark");
    expect(() => store.setTheme("light")).not.toThrow();
    expect(store.getSnapshot()).toBe("light");
  });

  it("[failure injection] storage.getItem/setItem throwing (private-mode Safari quota) does not break hydration or in-session state", () => {
    const throwingStorage = {
      getItem: () => {
        throw new Error("SecurityError: storage disabled");
      },
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
    };
    const root = fakeRoot();
    const store = createThemeStore({ storage: throwingStorage, root });

    expect(store.getSnapshot()).toBe("dark"); // hydrate() swallowed the read failure
    expect(() => store.setTheme("light")).not.toThrow();
    expect(store.getSnapshot()).toBe("light"); // in-memory state still updates
    expect(root._attrs.get("data-theme")).toBe("light");
  });

  it("[perf budget] creating 500 independent stores and toggling each stays under 500ms", () => {
    const started = Date.now();
    for (let i = 0; i < 500; i += 1) {
      const store = createThemeStore({ storage: fakeStorage(), root: fakeRoot() });
      store.getSnapshot();
      store.setTheme(i % 2 === 0 ? "light" : "dark");
    }
    const elapsed = Date.now() - started;
    expect(elapsed).toBeLessThan(500);
  });
});
