import { describe, expect, it } from "vitest";
import { computeFocusTrapTarget, getFocusableElements } from "./focus";

describe("getFocusableElements", () => {
  it("forwards the focusable-selector query to the container and returns it as a real array", () => {
    let receivedSelector = "";
    const fake = {
      querySelectorAll: (selector: string) => {
        receivedSelector = selector;
        return ["a", "b"];
      },
    };
    const result = getFocusableElements(fake);
    expect(result).toEqual(["a", "b"]);
    expect(receivedSelector).toContain("button:not(:disabled)");
    expect(receivedSelector).toContain('[tabindex]:not([tabindex="-1"])');
  });

  it("returns an empty array when the container has no focusable descendants", () => {
    const fake = { querySelectorAll: () => [] };
    expect(getFocusableElements(fake)).toEqual([]);
  });
});

describe("computeFocusTrapTarget", () => {
  const elements = ["first", "middle", "last"];

  it("[negative] a non-Tab key never redirects focus, even at a boundary", () => {
    expect(computeFocusTrapTarget({ key: "Enter", shiftKey: false }, elements, "last")).toBeNull();
  });

  it("[negative] Tab in the middle of the trap does not redirect focus", () => {
    expect(computeFocusTrapTarget({ key: "Tab", shiftKey: false }, elements, "middle")).toBeNull();
  });

  it("[negative] an empty element list never redirects focus, even for Tab", () => {
    expect(computeFocusTrapTarget({ key: "Tab", shiftKey: false }, [], "anything")).toBeNull();
  });

  it("Tab forward from the last element wraps to the first", () => {
    expect(computeFocusTrapTarget({ key: "Tab", shiftKey: false }, elements, "last")).toBe("first");
  });

  it("Shift+Tab from the first element wraps to the last", () => {
    expect(computeFocusTrapTarget({ key: "Tab", shiftKey: true }, elements, "first")).toBe("last");
  });

  it("[gate-red repro] a stale activeElement (e.g. removed from the DOM mid-trap) that doesn't match either boundary is left alone", () => {
    expect(computeFocusTrapTarget({ key: "Tab", shiftKey: false }, elements, null)).toBeNull();
  });

  it("a single-element trap wraps Tab and Shift+Tab back to itself", () => {
    expect(computeFocusTrapTarget({ key: "Tab", shiftKey: false }, ["only"], "only")).toBe("only");
    expect(computeFocusTrapTarget({ key: "Tab", shiftKey: true }, ["only"], "only")).toBe("only");
  });
});
