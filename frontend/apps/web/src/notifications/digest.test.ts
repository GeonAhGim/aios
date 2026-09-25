import { describe, expect, it } from "vitest";
import type { NotificationHistoryEntry } from "@aios/shared-types";
import { perfBudgetMs } from "../test/perfBudget";
import { UNKNOWN_DATE_KEY, computeDailyDigest } from "./digest";

function entry(overrides: Partial<NotificationHistoryEntry> = {}): NotificationHistoryEntry {
  return {
    eventType: "risk_mismatch",
    channel: "EMAIL",
    status: "SENT",
    createdAt: "2026-09-15T10:00:00.000Z",
    ...overrides,
  };
}

describe("computeDailyDigest: negative", () => {
  // negative 1
  it("빈 이력은 빈 다이제스트를 반환한다(오류 아님, FD-17.3 관용과 동일)", () => {
    expect(computeDailyDigest([])).toEqual([]);
  });

  // negative 2
  it("서버가 새 채널 값을 추가해도 죽지 않고 그 값 그대로 byChannel에 집계한다", () => {
    const entries = [entry({ channel: "SMS" as NotificationHistoryEntry["channel"] })];
    const digest = computeDailyDigest(entries);
    expect(digest).toHaveLength(1);
    expect(digest[0].byChannel).toEqual({ SMS: 1 });
    expect(digest[0].total).toBe(1);
  });

  // negative 3
  it("특정 채널 이력이 전혀 없으면 그 채널 키 자체가 byChannel에 생기지 않는다(0으로 채우지 않음)", () => {
    const digest = computeDailyDigest([entry({ channel: "EMAIL" })]);
    expect(digest[0].byChannel.PUSH).toBeUndefined();
    expect(Object.keys(digest[0].byChannel)).toEqual(["EMAIL"]);
  });

  // negative 4
  it("SENT/FAILED 둘 다 아닌 status는 total에는 잡히지만 sentCount/failedCount 어디에도 잡히지 않는다", () => {
    const digest = computeDailyDigest([entry({ status: "PENDING" as NotificationHistoryEntry["status"] })]);
    expect(digest[0].total).toBe(1);
    expect(digest[0].sentCount).toBe(0);
    expect(digest[0].failedCount).toBe(0);
  });
});

describe("computeDailyDigest: 실패 주입", () => {
  // 실패 주입: 서버/직렬화 손상으로 createdAt이 파싱 불가한 문자열로 온 경우
  // (예: 타임존 마이그레이션 버그) — 이 한 건이 throw로 전체 다이제스트 렌더링을
  // 막지 않고, 나머지 정상 이력의 날짜별 집계도 오염시키지 않아야 한다.
  it("createdAt이 파싱 불가하면 UNKNOWN_DATE_KEY로 격리하고, 다른 날짜 그룹은 그대로 정상 집계한다", () => {
    const entries = [
      entry({ createdAt: "not-a-date", eventType: "corrupted" }),
      entry({ createdAt: "2026-09-15T10:00:00.000Z" }),
      entry({ createdAt: "2026-09-14T10:00:00.000Z" }),
    ];

    const digest = computeDailyDigest(entries);

    const unknown = digest.find((g) => g.date === UNKNOWN_DATE_KEY);
    expect(unknown?.total).toBe(1);
    expect(unknown?.entries[0].eventType).toBe("corrupted");

    const sep15 = digest.find((g) => g.date === "2026-09-15");
    const sep14 = digest.find((g) => g.date === "2026-09-14");
    expect(sep15?.total).toBe(1);
    expect(sep14?.total).toBe(1);
  });
});

describe("computeDailyDigest: 수치 성능 단언(spec 아키텍처: apps/web/src/notifications/digest.ts)", () => {
  function bigHistory(n: number): NotificationHistoryEntry[] {
    const channels: NotificationHistoryEntry["channel"][] = ["EMAIL", "PUSH", "IN_APP"];
    const out: NotificationHistoryEntry[] = [];
    for (let i = 0; i < n; i += 1) {
      const day = 1 + (i % 28);
      out.push(
        entry({
          eventType: `event_${i % 50}`,
          channel: channels[i % channels.length],
          status: i % 7 === 0 ? "FAILED" : "SENT",
          createdAt: `2026-09-${String(day).padStart(2, "0")}T00:00:00.000Z`,
        }),
      );
    }
    return out;
  }

  // 실 사용자 알림 이력(수백~수천 건)보다 훨씬 큰 입력(n=50000)으로 O(n) 상한을
  // 못박는다 — 예산 초과는 Map 대신 배열 탐색(indexOf/find)으로 그룹을 찾는
  // 패턴으로 퇴행했다는 신호다.
  it("대량 입력(n=50000)도 100ms 예산 안에서 끝난다", () => {
    const input = bigHistory(50000);

    const start = performance.now();
    const digest = computeDailyDigest(input);
    const elapsedMs = performance.now() - start;

    expect(digest.length).toBeGreaterThan(0);
    expect(elapsedMs).toBeLessThan(perfBudgetMs(100));
  });

  // 게이트 적색 재현: 실제 구현(Map 기반 O(n))과 달리 매 이력마다 배열을
  // find로 훑어 그룹을 찾는(그룹 수만큼이 아니라 이력 수만큼 배열이 자라는)
  // O(n^2) 회귀 구현을 같은 입력으로 대조한다 — computeDailyDigest가 이
  // 패턴으로 퇴행하면 이 테스트가 즉시 실패로 드러남을 보증한다.
  function o2RegressionComputeDailyDigest(entries: NotificationHistoryEntry[]): DailyDigestGroupLike[] {
    const groups: DailyDigestGroupLike[] = [];
    for (const e of entries) {
      const parsed = new Date(e.createdAt);
      const key = Number.isNaN(parsed.getTime()) ? UNKNOWN_DATE_KEY : parsed.toISOString().slice(0, 10);
      let group = groups.find((g) => g.date === key);
      if (!group) {
        group = { date: key, total: 0 };
        groups.push(group);
      }
      group.total += 1;
    }
    return groups;
  }
  interface DailyDigestGroupLike {
    date: string;
    total: number;
  }

  // O(n^2) 재현에는 날짜(그룹)마다 배열이 자라야 실제 이력 규모에서도 격차가
  // 드러난다 — bigHistory는 28일로 그룹 수를 고정해 real/regression 둘 다
  // 빠르므로, 이 테스트는 이력마다 서로 다른 날짜(그룹 수 = n)를 쓰는 전용
  // 픽스처로 대조한다.
  function uniqueDayHistory(n: number): NotificationHistoryEntry[] {
    const base = Date.UTC(2020, 0, 1);
    const out: NotificationHistoryEntry[] = [];
    for (let i = 0; i < n; i += 1) {
      out.push(entry({ createdAt: new Date(base + i * 86_400_000).toISOString() }));
    }
    return out;
  }

  it("실제 구현은 예산 안에 끝나지만, O(n^2) 회귀 구현은 같은 입력에서 예산을 초과한다(게이트 적색)", () => {
    const input = uniqueDayHistory(10000);
    const budget = perfBudgetMs(100);

    const realStart = performance.now();
    computeDailyDigest(input);
    const realElapsedMs = performance.now() - realStart;

    const regressionStart = performance.now();
    o2RegressionComputeDailyDigest(input);
    const regressionElapsedMs = performance.now() - regressionStart;

    expect(realElapsedMs).toBeLessThan(budget);
    expect(regressionElapsedMs).toBeGreaterThan(budget);
  });
});
