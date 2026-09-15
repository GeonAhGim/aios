import { describe, expect, it } from "vitest";
import { describeScreenDefinitionIssues, type ScreenDefinitionInput } from "./screener";

function definition(overrides: Partial<ScreenDefinitionInput> = {}): ScreenDefinitionInput {
  return {
    universe: "KRX",
    filters: [{ kind: "indicator", field: "rsi_14", operator: "lt", value: "30" }],
    sort: null,
    columns: ["symbol"],
    ...overrides,
  };
}

describe("describeScreenDefinitionIssues", () => {
  it("유효한 정의는 위반이 0건이다", () => {
    expect(describeScreenDefinitionIssues(definition())).toEqual([]);
  });

  it("negative 1/3: universe가 빈 문자열이면 universe_required를 반환한다", () => {
    expect(describeScreenDefinitionIssues(definition({ universe: "  " }))).toEqual([
      { code: "universe_required" },
    ]);
  });

  it("negative 2/3: 필터가 0건이면 filters_required를 반환한다", () => {
    expect(describeScreenDefinitionIssues(definition({ filters: [] }))).toEqual([
      { code: "filters_required" },
    ]);
  });

  it("negative 3/3: 필터의 field·value가 비어 있으면 filter_incomplete(index)를 반환한다", () => {
    expect(
      describeScreenDefinitionIssues(
        definition({
          filters: [
            { kind: "indicator", field: "rsi_14", operator: "lt", value: "30" },
            { kind: "fundamental", field: "", operator: "gt", value: "" },
          ],
        }),
      ),
    ).toEqual([{ code: "filter_incomplete", index: 1 }]);
  });

  // 게이트적색: spec §3 "ResearchFilter는 as_of 없이 실행 불가"(누수 차단, RD-A1
  // 상속)를 정확히 겨눈다 — describeScreenDefinitionIssues의 research 분기(as_of
  // 누락 검사)를 지우면 이 테스트만 적색이 된다.
  it("게이트적색 1/2: research 필터에 as_of가 없으면 filter_research_as_of_required(index)를 반환한다", () => {
    expect(
      describeScreenDefinitionIssues(
        definition({
          filters: [{ kind: "research", field: "sentiment_score", operator: "gte", value: "0.5" }],
        }),
      ),
    ).toEqual([{ code: "filter_research_as_of_required", index: 0 }]);
  });

  it("게이트적색 2/2: research 필터에 as_of가 있으면(공백만도 아님) 통과한다", () => {
    expect(
      describeScreenDefinitionIssues(
        definition({
          filters: [
            {
              kind: "research",
              field: "sentiment_score",
              operator: "gte",
              value: "0.5",
              asOf: "2026-09-01T00:00:00Z",
            },
          ],
        }),
      ),
    ).toEqual([]);
  });

  // 실패주입: field/value는 있지만 as_of만 공백 문자열인 research 필터 — trim 없이
  // 진리값만 검사했다면 통과했을 경계를 잡는다.
  it("실패주입: as_of가 공백 문자열뿐이면 여전히 filter_research_as_of_required로 잡힌다", () => {
    expect(
      describeScreenDefinitionIssues(
        definition({
          filters: [
            { kind: "research", field: "sentiment_score", operator: "gte", value: "0.5", asOf: "   " },
          ],
        }),
      ),
    ).toEqual([{ code: "filter_research_as_of_required", index: 0 }]);
  });

  // 성능: 필터 500건도 예산 시간 안에 검증한다(선형 스캔에서 벗어나면 잡는다).
  it("성능: 필터 500건도 예산 시간 안에 검증한다", () => {
    const filters = Array.from({ length: 500 }, (_, i) => ({
      kind: "indicator" as const,
      field: `indicator_${i}`,
      operator: "gt" as const,
      value: "1",
    }));
    const startedAt = performance.now();
    const issues = describeScreenDefinitionIssues(definition({ filters }));
    const elapsedMs = performance.now() - startedAt;

    expect(issues).toEqual([]);
    expect(elapsedMs).toBeLessThan(200);
  });
});
