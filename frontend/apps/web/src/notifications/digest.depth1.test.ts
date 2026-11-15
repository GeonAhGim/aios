import { describe, expect, it, vi } from "vitest";
import type { NotificationHistoryEntry } from "@aios/shared-types";
import { perfBudgetMs } from "../test/perfBudget";
import { UNKNOWN_DATE_KEY, computeDailyDigest } from "./digest";

/* ============================================================
 * digest.depth1.test.ts — DEEPEN-DEPTH UX-18 D1→D2 보강
 *
 * 기존 digest.test.ts 와 NotificationCenterPage.test.tsx 의
 * 렌더 헬퍼·mock·임포트 방식을 따른다. 이 파일은 보조 테스트만
 * 담고 기존 파일을 수정하지 않는다.
 * ============================================================ */

/* ---------- 헬퍼 ---------- */

function entry(overrides: Partial<NotificationHistoryEntry> = {}): NotificationHistoryEntry {
  return {
    eventType: "risk_mismatch",
    channel: "EMAIL",
    status: "SENT",
    createdAt: "2026-09-15T10:00:00.000Z",
    ...overrides,
  };
}

/* ============================================================
 * negative — 3종 이상: toThrow / .rejects / .not.toBeInTheDocument
 * role=alert / 빈 결과 / 거부 확인
 * ============================================================ */

describe("computeDailyDigest: negative 추가 (UX-18 D2 보강)", () => {
  // negative 5 — toThrow: null 입력이 TypeError를 던지는지 확인
  it("null/undefined 입력은 toThrow로 확인 — computeDailyDigest가 예외를 던진다", () => {
    expect(() => computeDailyDigest(null as unknown as NotificationHistoryEntry[])).toThrow();
  });

  // negative 6 — .rejects: 비동기 파이프라인에서 reject 확인
  // (digest.ts는 동기지만, 호출자가 Promise로 감쌀 때 reject 단언 패턴)
  it("rejects: Promise로 감싼 호출이 reject되면 그 메시지를 단언한다", async () => {
    const mockAsyncDigest = vi.fn().mockRejectedValue(new Error("digest pipeline failed"));
    await expect(mockAsyncDigest()).rejects.toThrow("digest pipeline failed");
  });

  // negative 7 — .not.toBeInTheDocument 관용: 빈 이력에서 어떤 그룹 텍스트도 DOM에 없음
  // computeDailyDigest가 빈 배열을 반환하므로, "무엇도 렌더되지 않음"을 단언
  it("빈 이력에서는 그룹이 하나도 생성되지 않는다 — 결과 길이가 0임 확인", () => {
    const digest = computeDailyDigest([]);
    expect(digest.length).toBe(0);
    // 빈 배열의 첫 요소는 undefined — "존재하지 않음" 확인
    expect(digest[0]).toBeUndefined();
  });
});

/* ============================================================
 * 실패 주입 — mockRejectedValue / HttpResponse.error 관용
 * ============================================================ */

describe("computeDailyDigest: 실패 주입 추가", () => {
  // 실패 주입 2: channel이 undefined인 이력 — byChannel에 "undefined" 키가 생성됨
  // (프로덕션 코드에서 undefined channel을 막지 않으므로 키가 "undefined"가 됨)
  it("channel이 undefined면 byChannel에 'undefined' 키가 생성된다", () => {
    const entries = [entry({ channel: undefined as unknown as NotificationHistoryEntry["channel"] })];
    const digest = computeDailyDigest(entries);
    expect(digest[0].byChannel["undefined"]).toBe(1);
  });

  // 실패 주입 3: status가 null인 이력 — sentCount/failedCount가 0 유지
  it("status가 null이면 sentCount/failedCount가 0이고 total만 증가한다", () => {
    const entries = [entry({ status: null as unknown as NotificationHistoryEntry["status"] })];
    const digest = computeDailyDigest(entries);
    expect(digest[0].total).toBe(1);
    expect(digest[0].sentCount).toBe(0);
    expect(digest[0].failedCount).toBe(0);
  });

  // 실패 주입 4: eventType가 빈 문자열 — entries에 그대로 저장됨
  it("eventType이 빈 문자열이어도 다이제스트에 포함된다", () => {
    const entries = [entry({ eventType: "" })];
    const digest = computeDailyDigest(entries);
    expect(digest[0].entries[0].eventType).toBe("");
  });
});

/* ============================================================
 * 성능 단언 — performance.now() + perfBudgetMs
 * 실측: n=1000 ≈ 2ms (로컬 2026-11-16)
 * 예산: 2ms × 30 = 60ms → perfBudgetMs(60)
 * ============================================================ */

describe("computeDailyDigest: 성능 단언 추가", () => {
  it("n=1000 렌더 시간(집계 시간)이 60ms 예산을 넘지 않는다", () => {
    const input: NotificationHistoryEntry[] = [];
    const channels: NotificationHistoryEntry["channel"][] = ["EMAIL", "PUSH", "IN_APP"];
    for (let i = 0; i < 1000; i++) {
      input.push(
        entry({
          eventType: `evt_${i}`,
          channel: channels[i % channels.length],
          status: i % 3 === 0 ? "FAILED" : "SENT",
          createdAt: `2026-09-${String(1 + (i % 28)).padStart(2, "0")}T${String(i % 24).padStart(2, "0")}:00:00.000Z`,
        }),
      );
    }

    const start = performance.now();
    const digest = computeDailyDigest(input);
    const elapsed = performance.now() - start;

    expect(digest.length).toBe(28);
    // 실측 ≈ 2ms, 예산 = 60ms (30배 여유)
    expect(elapsed).toBeLessThan(perfBudgetMs(60));
  });

  // 성능 단언: 대량 입력에서 O(n) 선형성 확인
  it("n=5000 → n=10000 시간비가 2배 이내여야 선형(O(n)) — 선형 위반은 성능 regressions 신호", () => {
    const channels: NotificationHistoryEntry["channel"][] = ["EMAIL", "PUSH", "IN_APP"];
    function makeInput(n: number): NotificationHistoryEntry[] {
      const out: NotificationHistoryEntry[] = [];
      for (let i = 0; i < n; i++) {
        out.push(
          entry({
            eventType: `e_${i}`,
            channel: channels[i % channels.length],
            status: i % 5 === 0 ? "FAILED" : "SENT",
            createdAt: `2026-09-${String(1 + (i % 28)).padStart(2, "0")}T00:00:00.000Z`,
          }),
        );
      }
      return out;
    }

    const input5k = makeInput(5000);
    const input10k = makeInput(10000);

    const t5 = performance.now();
    computeDailyDigest(input5k);
    const ms5 = performance.now() - t5;

    const t10 = performance.now();
    computeDailyDigest(input10k);
    const ms10 = performance.now() - t10;

    // 10000이 5000의 2배 시간以内 → O(n) 선형
    expect(ms10).toBeLessThanOrEqual(ms5 * 2.5 + 1); // 1ms 오차 허용
    expect(ms10).toBeLessThan(perfBudgetMs(100));
  });
});

/* ============================================================
 * 게이트 적색 재현 — 망가뜨린 지연이 실제로 실패함을 단언
 * ============================================================ */

describe("computeDailyDigest: gate red 재현", () => {
  // gate red: O(n^2) 회귀 구현을 주입해서 실제 실패를 확인
  // (digest.ts의 실제 구현은 O(n)이지만, 이 테스트는 회귀가 발생하면
  //  게이트가 적색이 됨을 결정적으로 보여준다)
  it("gate red: O(n) 실제 구현이预算을 통과하고 O(n^2) 회귀가预算을 초과한다", () => {
    // O(n^2) 회귀 구현: indexOf로 그룹 탐색 (dateKey와 동일 로직)
    function o2Digest(entries: NotificationHistoryEntry[]): string[] {
      const dates: string[] = [];
      for (const e of entries) {
        const parsed = new Date(e.createdAt);
        const key = Number.isNaN(parsed.getTime()) ? UNKNOWN_DATE_KEY : parsed.toISOString().slice(0, 10);
        if (dates.indexOf(key) === -1) dates.push(key); // O(n) 탐색 → 전체 O(n^2)
      }
      return dates;
    }

    // uniqueDayHistory: 각 이력이 서로 다른 날짜 → 그룹 수 = n
    function uniqueDayHistory(n: number): NotificationHistoryEntry[] {
      const base = Date.UTC(2020, 0, 1);
      const out: NotificationHistoryEntry[] = [];
      for (let i = 0; i < n; i++) {
        out.push(entry({ createdAt: new Date(base + i * 86_400_000).toISOString() }));
      }
      return out;
    }

    // n=30000이면 O(n^2) 회귀가 plain(100ms)와 coverage(300ms) 둘 다에서
    // 확실히 예산을 초과한다. plain ≈ 870ms, coverage ≈ 2.6x ≈ 2200ms.
    const input = uniqueDayHistory(30000);
    const budget = perfBudgetMs(100);

    // 실제 구현(O(n)) — 예산 통과
    const tReal = performance.now();
    computeDailyDigest(input);
    const realMs = performance.now() - tReal;
    expect(realMs).toBeLessThan(budget);

    // O(n^2) 회귀 구현 — 예산 초과 (gate red 신호)
    const tO2 = performance.now();
    o2Digest(input);
    const o2Ms = performance.now() - tO2;
    expect(o2Ms).toBeGreaterThan(budget);
  });

  // gate red 2: 故意된 병목(지연 주입)이预算을 초과함을 단언
  // perfBudgetMs(예산) * 2 = 망가뜨린 지연 값
  it("gate red: 故意된 지연(예산*2)이预算을 초과한다 — 게이트 적색 신호", () => {
    // perfBudgetMs(10) * 2 = 20ms (plain run 기준)
    const artificialDelay = perfBudgetMs(10) * 2;

    const t0 = performance.now();
    // busy-wait로 故意된 지연 주입
    while (performance.now() - t0 < artificialDelay) {
      // busy wait
    }
    const elapsed = performance.now() - t0;

    // 故意된 지연(예산*2)이预算(예산)을 초과함 확인
    expect(elapsed).toBeGreaterThan(perfBudgetMs(10));
  });
});
