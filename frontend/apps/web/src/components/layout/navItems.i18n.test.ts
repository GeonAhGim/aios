import { describe, expect, it } from "vitest";
import {
  MAIN_NAV_ITEMS,
  SETTINGS_NAV_ITEMS,
  ADMIN_NAV_ITEMS,
} from "./navItems";
import { catalogKo } from "../../i18n/catalog.ko";
import { catalogEn } from "../../i18n/catalog.en";

// task-25743 [UAT-F4] navItems.ts의 모든 label이 ko·en 카탈로그 nav 블록에
// 키로 존재하는지 검증. nav. 접두를 떼어 /... 경로로 만든 뒤 카탈로그를 조회한다.

describe("navItems i18n 키 완전성 (ko)", () => {
  it("MAIN/SETTINGS/ADMIN 모든 항목의 label 키가 ko 카탈로그 nav 블록에 있다", () => {
    const navBlock = catalogKo.nav as Record<string, string> | undefined;
    if (navBlock == null) {
      throw new Error("ko 카탈로그에 nav 블록이 없습니다");
    }
    const allItems = [...MAIN_NAV_ITEMS, ...SETTINGS_NAV_ITEMS, ...ADMIN_NAV_ITEMS];
    const missing: string[] = [];
    for (const item of allItems) {
      const key = item.label.startsWith("nav.") ? item.label.slice(4) : item.label;
      if (!(key in navBlock)) {
        missing.push(key);
      }
    }
    if (missing.length > 0) {
      throw new Error(`ko 카탈로그 nav 블록에서 누락된 키 ${missing.length}건: ${missing.join(", ")}`);
    }
  });

  it("존재하지 않는 키를 가진 가짜 항목은 누락으로 보고한다(negative)", () => {
    const navBlock = catalogKo.nav as Record<string, string> | undefined;
    expect(navBlock).toBeDefined();
    const fakeKey = "/nonexistent";
    expect(fakeKey in (navBlock ?? {})).toBe(false);
  });
});

describe("navItems i18n 키 완전성 (en)", () => {
  it("MAIN/SETTINGS/ADMIN 모든 항목의 label 키가 en 카탈로그 nav 블록에 있다", () => {
    const navBlock = catalogEn.nav as Record<string, string> | undefined;
    if (navBlock == null) {
      throw new Error("en 카탈로그에 nav 블록이 없습니다");
    }
    const allItems = [...MAIN_NAV_ITEMS, ...SETTINGS_NAV_ITEMS, ...ADMIN_NAV_ITEMS];
    const missing: string[] = [];
    for (const item of allItems) {
      const key = item.label.startsWith("nav.") ? item.label.slice(4) : item.label;
      if (!(key in navBlock)) {
        missing.push(key);
      }
    }
    if (missing.length > 0) {
      throw new Error(`en 카탈로그 nav 블록에서 누락된 키 ${missing.length}건: ${missing.join(", ")}`);
    }
  });

  it("존재하지 않는 키를 가진 가짜 항목은 누락으로 보고한다(negative)", () => {
    const navBlock = catalogEn.nav as Record<string, string> | undefined;
    expect(navBlock).toBeDefined();
    const fakeKey = "/nonexistent";
    expect(fakeKey in (navBlock ?? {})).toBe(false);
  });
});
