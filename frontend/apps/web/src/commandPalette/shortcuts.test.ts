import { describe, expect, it } from "vitest";
import { SHORTCUT_ENTRIES } from "./shortcuts";

describe("SHORTCUT_ENTRIES: negative", () => {
  it("키 조합이 중복되지 않는다(도움말에 같은 단축키가 두 번 뜨면 사용자가 혼란스럽다)", () => {
    const keys = SHORTCUT_ENTRIES.map((entry) => entry.keys);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("모든 항목이 빈 문자열이 아닌 keys를 갖고 id도 중복되지 않는다", () => {
    const ids = SHORTCUT_ENTRIES.map((entry) => entry.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const entry of SHORTCUT_ENTRIES) {
      expect(entry.keys.length).toBeGreaterThan(0);
    }
  });

  it("최소 하나 이상의 단축키가 등록돼 있다(도움말이 비어 보이지 않는다)", () => {
    expect(SHORTCUT_ENTRIES.length).toBeGreaterThan(0);
  });
});
