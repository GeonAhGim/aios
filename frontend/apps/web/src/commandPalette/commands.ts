import type { NavItem } from "../components/layout/navItems";
import type { Command } from "./matchCommands";

/** MAIN/SETTINGS/ADMIN_NAV_ITEMS(navItems.ts)를 명령 팔레트 항목으로 그대로 변환한다
 * -- nav 링크가 곧 "키보드로 도달 가능한 경로" 전 목록이라는 UX-19 DoD의 근거다. */
export function buildNavCommands(items: readonly NavItem[]): Command[] {
  return items.map((item) => ({ id: item.to, label: item.label, to: item.to, keywords: item.to }));
}
