import { describe, expect, it } from "vitest";
import { perfBudgetMs } from "../test/perfBudget";
import { matchCommands, type Command } from "./matchCommands";

function command(overrides: Partial<Command> = {}): Command {
  return {
    id: "dashboard",
    label: "대시보드",
    to: "/dashboard",
    ...overrides,
  };
}

describe("matchCommands: negative", () => {
  // negative 1
  it("빈 쿼리는 입력 순서 그대로 전체 명령을 반환한다", () => {
    const commands = [command({ id: "a" }), command({ id: "b", label: "포트폴리오", to: "/portfolio" })];
    expect(matchCommands(commands, "")).toEqual(commands);
  });

  // negative 2
  it("공백만 있는 쿼리도 빈 쿼리와 동일하게 취급한다(trim)", () => {
    const commands = [command()];
    expect(matchCommands(commands, "   ")).toEqual(commands);
  });

  // negative 3
  it("명령 목록이 비어 있으면 쿼리가 있어도 빈 배열을 반환한다(오류 아님)", () => {
    expect(matchCommands([], "대시보드")).toEqual([]);
  });

  // negative 4
  it("어느 필드에도 없는 쿼리는 빈 배열을 반환한다(오류 아님)", () => {
    const commands = [command()];
    expect(matchCommands(commands, "존재하지않는단어")).toEqual([]);
  });

  // negative 5
  it("대소문자·라벨 대신 keywords/경로로도 매칭된다(별칭 검색)", () => {
    const commands = [command({ label: "지갑", to: "/wallet", keywords: "wallet payout" })];
    expect(matchCommands(commands, "PAYOUT")).toHaveLength(1);
    expect(matchCommands(commands, "/wallet")).toHaveLength(1);
  });
});

describe("matchCommands: 실패 주입", () => {
  // 실패 주입: 서버/상위 목록이 손상되어 label이 undefined인 항목이 섞여 온 경우
  // (예: nav 항목 마이그레이션 중 누락) -- 이 한 건이 throw로 팔레트 검색 전체를
  // 막지 않고, 나머지 정상 명령의 매칭도 오염시키지 않아야 한다.
  it("label이 undefined인 손상된 명령이 섞여도 throw하지 않고 그 항목만 결과에서 빠진다", () => {
    const corrupted = command({ id: "broken", label: undefined as unknown as string, to: "/broken" });
    const healthy = command({ id: "dashboard", label: "대시보드", to: "/dashboard" });
    const commands = [corrupted, healthy];

    expect(() => matchCommands(commands, "대시보드")).not.toThrow();
    const result = matchCommands(commands, "대시보드");
    expect(result).toEqual([healthy]);
  });
});

describe("matchCommands: 수치 성능 단언(spec 아키텍처: apps/web/src/commandPalette/matchCommands.ts)", () => {
  function bigCommandList(n: number): Command[] {
    const out: Command[] = [];
    for (let i = 0; i < n; i += 1) {
      out.push(command({ id: `cmd-${i}`, label: `화면 ${i}`, to: `/screen-${i}` }));
    }
    return out;
  }

  // 실 사용자 nav 항목(수십 건)보다 훨씬 큰 입력(n=5000)으로 O(n log n) 상한을
  // 못박는다.
  it("대량 입력(n=5000)도 50ms 예산 안에서 끝난다", () => {
    const commands = bigCommandList(5000);

    const start = performance.now();
    const result = matchCommands(commands, "화면 1");
    const elapsedMs = performance.now() - start;

    expect(result.length).toBeGreaterThan(0);
    expect(elapsedMs).toBeLessThan(perfBudgetMs(50));
  });

  // 게이트 적색 재현: 실제 구현(Array.sort 기반 O(n log n))과 달리, 매 라운드
  // 남은 항목 중 최고 점수를 선형 탐색으로 골라내는 선택정렬 방식(O(n^2))으로
  // 정렬하는 회귀 구현을 같은 입력으로 대조한다. matchCommands가 이 패턴으로
  // 퇴행하면 이 테스트가 즉시 실패로 드러남을 보증한다.
  function selectionSortRegressionMatch(commands: readonly Command[], query: string): Command[] {
    const needle = query.trim().toLowerCase();
    if (needle === "") return [...commands];
    const remaining: { command: Command; score: number }[] = [];
    for (const c of commands) {
      const haystack = `${(c.label ?? "").toLowerCase()} ${(c.keywords ?? "").toLowerCase()} ${c.to.toLowerCase()}`;
      const score = haystack.indexOf(needle);
      if (score !== -1) remaining.push({ command: c, score });
    }
    const out: Command[] = [];
    while (remaining.length > 0) {
      let bestIdx = 0;
      for (let i = 1; i < remaining.length; i += 1) {
        if (remaining[i]!.score < remaining[bestIdx]!.score) bestIdx = i;
      }
      out.push(remaining.splice(bestIdx, 1)[0]!.command);
    }
    return out;
  }

  // 모든 항목이 매칭돼야(공통 접두사) 회귀 구현의 while 루프가 매 라운드
  // 전체를 선형 탐색해 실제 O(n^2) 격차가 드러난다.
  function uniqueMatchingList(n: number): Command[] {
    const out: Command[] = [];
    for (let i = 0; i < n; i += 1) {
      out.push(command({ id: `cmd-${i}`, label: `entry-${String(i).padStart(6, "0")}`, to: `/e-${i}` }));
    }
    return out;
  }

  it("실제 구현은 예산 안에 끝나지만, 선택정렬 O(n^2) 회귀 구현은 같은 입력에서 예산을 초과한다(게이트 적색)", () => {
    const commands = uniqueMatchingList(15000);
    const budget = perfBudgetMs(50);

    const realStart = performance.now();
    matchCommands(commands, "entry-");
    const realElapsedMs = performance.now() - realStart;

    const regressionStart = performance.now();
    selectionSortRegressionMatch(commands, "entry-");
    const regressionElapsedMs = performance.now() - regressionStart;

    expect(realElapsedMs).toBeLessThan(budget);
    expect(regressionElapsedMs).toBeGreaterThan(budget);
  });
});
