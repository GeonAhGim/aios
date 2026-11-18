import { describe, expect, it } from "vitest";
import { catalogEn } from "../../i18n/catalog.en";
import { catalogKo } from "../../i18n/catalog.ko";
import { ALL_NAV_ITEMS, type NavItem } from "./navItems";

// UAT-F4. A nav label that has no catalog entry is rendered as the raw key
// (e.g. "nav./signals/sources"), so every label must resolve in both locales.
const NAV_PREFIX = "nav.";

export function findMissingNavKeys(items: readonly NavItem[], navBlock: Record<string, string>): string[] {
  return items
    .map((item) => (item.label.startsWith(NAV_PREFIX) ? item.label.slice(NAV_PREFIX.length) : item.label))
    .filter((key) => !(key in navBlock));
}

describe("navItems label -> catalog nav block", () => {
  it("ko catalog has every nav key", () => {
    expect(findMissingNavKeys(ALL_NAV_ITEMS, catalogKo.nav)).toEqual([]);
  });

  it("en catalog has every nav key", () => {
    expect(findMissingNavKeys(ALL_NAV_ITEMS, catalogEn.nav)).toEqual([]);
  });

  it("negative: reports every key absent from the catalog", () => {
    const fake: NavItem[] = [
      { to: "/dashboard", label: "nav./dashboard" },
      { to: "/nope", label: "nav./nope" },
      { to: "/nope2", label: "nav./nope2" },
    ];
    expect(findMissingNavKeys(fake, catalogKo.nav)).toEqual(["/nope", "/nope2"]);
  });
});
