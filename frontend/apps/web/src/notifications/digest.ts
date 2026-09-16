import type { NotificationHistoryEntry } from "@aios/shared-types";

// UX-18 DoD(이력·요약): NotificationCenterPage가 이미 받아온 이력(FD-17.3
// getNotificationHistory)을 날짜별로 묶어 "일간 다이제스트" 요약을 만든다.
// 순수 함수로 분리한 이유는 push.ts(UX-17)의 urlBase64ToUint8Array와 동일 —
// 네트워크/훅과 분리해야 ADR-2026-09-09-C D2(수치 성능 단언·게이트 적색 재현)를
// 결정적으로(deterministic) 증빙할 수 있다.

export const UNKNOWN_DATE_KEY = "unknown";

export interface DailyDigestGroup {
  date: string;
  total: number;
  sentCount: number;
  failedCount: number;
  byChannel: Record<string, number>;
  entries: NotificationHistoryEntry[];
}

/** createdAt이 파싱 불가하면(실패 주입 대상) UNKNOWN_DATE_KEY로 격리한다 — 한 건의
 * 손상된 이력이 나머지 날짜의 집계·정렬을 무너뜨리지 않게 하기 위함. */
function dateKey(createdAt: string): string {
  const parsed = new Date(createdAt);
  if (Number.isNaN(parsed.getTime())) return UNKNOWN_DATE_KEY;
  return parsed.toISOString().slice(0, 10);
}

/** O(n) 단일 패스 — Map 기반 그룹핑. 날짜 수(보통 수십 개)만 정렬하므로
 * 입력 크기와 무관하게 최종 정렬 비용은 무시할 수 있다. */
export function computeDailyDigest(entries: NotificationHistoryEntry[]): DailyDigestGroup[] {
  const groups = new Map<string, DailyDigestGroup>();

  for (const entry of entries) {
    const key = dateKey(entry.createdAt);
    let group = groups.get(key);
    if (!group) {
      group = { date: key, total: 0, sentCount: 0, failedCount: 0, byChannel: {}, entries: [] };
      groups.set(key, group);
    }
    group.total += 1;
    group.byChannel[entry.channel] = (group.byChannel[entry.channel] ?? 0) + 1;
    if (entry.status === "SENT") group.sentCount += 1;
    else if (entry.status === "FAILED") group.failedCount += 1;
    group.entries.push(entry);
  }

  return [...groups.values()].sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
}
