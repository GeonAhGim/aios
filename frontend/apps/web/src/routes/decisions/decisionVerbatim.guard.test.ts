import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { perfBudgetMs } from "../../test/perfBudget";

// task-2706 UX-22: spec L4_product_experience_and_discovery_v1.0.md §4 UX-A5
// ("결정 이력 뷰어는 저장된 이벤트·판정만 표시한다(추론·요약 생성 금지)").
// errorSurface.guard.test.ts/tableOverflow.guard.test.ts와 같은 방식으로 이
// 원칙을 CI 게이트로 만든다: routes/decisions/**의 실제 렌더 소스(주석 제외)에
// 추론·요약을 뜻하는 토큰이 나타나면 위반으로 본다 — U-6/U-14 DoD가 같은
// 원칙을 "요약·추론을 생성하지 않음(UX-A5와 동형 정적 검사)"로 표현한 것과
// 동일 검사다.
const SCAN_DIR = dirname(fileURLToPath(import.meta.url));

// 토큰은 정규식 소스만 담고, 실행 시 항상 새 RegExp(...,"g")로 만든다 — 공유
// 인스턴스의 lastIndex가 파일 간에 새어 나가 두 번째 파일부터 검사를 건너뛰는
// 사고(정규식 stateful 버그의 전형)를 피한다.
const BANNED_TOKEN_SOURCES: string[] = [
  "추론",
  "요약",
  "인사이트",
  "종합\\s*판단",
  "AI\\s*(추천|해설|코멘트)",
  "summariz(e|ed|ing)",
  "infer[A-Z]\\w*\\(",
  "generate(Summary|Insight|Explanation)\\w*\\(",
];

function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
}

export function findInferenceViolations(source: string): number[] {
  const stripped = stripComments(source);
  const violations: number[] = [];
  for (const tokenSource of BANNED_TOKEN_SOURCES) {
    const pattern = new RegExp(tokenSource, "gi");
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(stripped)) !== null) {
      violations.push(match.index);
      if (match[0].length === 0) pattern.lastIndex += 1; // 무한루프 방지(zero-length 매치)
    }
  }
  return violations.sort((a, b) => a - b);
}

function listTsxFiles(dir: string): string[] {
  const files: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      files.push(...listTsxFiles(full));
    } else if (name.endsWith(".tsx") && !name.endsWith(".test.tsx")) {
      files.push(full);
    }
  }
  return files;
}

describe("findInferenceViolations — 스캐너 자체 검증", () => {
  it("negative: 서버가 준 verdict·ruleHits 필드를 그대로 렌더하면 위반이 아니다", () => {
    const source = `<p>{result.verdict}</p><span>{hit.ruleId}</span><p>{hit.message}</p>`;
    expect(findInferenceViolations(source)).toHaveLength(0);
  });

  it("negative: 주석에만 금칙어가 있으면(문서화 목적) 위반이 아니다", () => {
    const source = `// 이 컴포넌트는 요약이나 추론을 생성하지 않는다\nconst x = 1;`;
    expect(findInferenceViolations(source)).toHaveLength(0);
  });

  it("negative: '판정 조회'처럼 금칙어와 무관한 한국어 UI 문구는 위반이 아니다", () => {
    const source = `<h2>{t("decisions.eventLineage.heading")}</h2>`;
    expect(findInferenceViolations(source)).toHaveLength(0);
  });

  it("위반: 렌더 코드에 '요약'이 직접 나타나면 위반이다", () => {
    expect(findInferenceViolations(`<p>AI 요약: {result.verdict}</p>`)).toHaveLength(1);
  });

  it("위반: 클라이언트 쪽 summarize()/generateInsight() 호출은 위반이다", () => {
    expect(findInferenceViolations(`const text = summarize(result);`)).toHaveLength(1);
    expect(findInferenceViolations(`const note = generateInsight(hit);`)).toHaveLength(1);
  });

  it("항상 빈 배열을 돌려주는 무력화된 스캐너는 이 테스트로 걸러진다", () => {
    const alwaysEmptyScanner = () => [] as number[];
    const violatingSource = `<p>AI 추천: 매수 추천</p>`;
    expect(findInferenceViolations(violatingSource).length).toBeGreaterThan(0);
    expect(findInferenceViolations(violatingSource)).not.toEqual(alwaysEmptyScanner());
  });

  it("실패 주입: 향후 누군가 '결과를 한 줄 요약해주는 배지'를 이 화면에 추가하는 미래 회귀를 그대로 재현하면 스캐너가 잡아낸다", () => {
    // UX-A5가 명시적으로 금지하는 시나리오(스펙 §4) — 실제 커밋 이력은 없지만
    // (이 리프가 이 파일의 최초 도입이라) 스펙이 예견한 회귀 패턴을 합성해
    // "이론상 잡는다"가 아니라 "그 정확한 패턴을 잡는다"를 증명한다.
    const futureRegressionSnippet = `
      function verdictBadge(result: ComplianceDecisionView) {
        const overallRisk = result.ruleHits.some((h) => h.severity === "DENY") ? "위험" : "안전";
        return <Badge>{overallRisk} (AI 종합 판단)</Badge>;
      }
    `;
    expect(findInferenceViolations(futureRegressionSnippet).length).toBeGreaterThan(0);
  });

  it("perf: 500개 컴포넌트 분량 합성 소스를 스캔해도 예산 안에 끝난다(정규식 파국적 백트래킹 없음)", () => {
    const repeated = Array.from({ length: 500 }, (_, i) =>
      i % 2 === 0
        ? `<p>{result${i}.verdict}</p>`
        : `<p>AI 요약 ${i}</p>`,
    ).join("\n");

    const start = performance.now();
    const violations = findInferenceViolations(repeated);
    const elapsed = performance.now() - start;

    expect(violations).toHaveLength(250);
    expect(elapsed).toBeLessThan(perfBudgetMs(200));
  });
});

describe("게이트 적색 재현 — 실제 파일 스캔", () => {
  it("routes/decisions/**/*.tsx 전체에 추론·요약 토큰이 0건이다(green)", () => {
    const violations: string[] = [];
    for (const file of listTsxFiles(SCAN_DIR)) {
      const source = readFileSync(file, "utf-8");
      const found = findInferenceViolations(source);
      if (found.length > 0) {
        violations.push(`${relative(SCAN_DIR, file).replace(/\\/g, "/")}: ${found.length}건`);
      }
    }
    expect(violations).toEqual([]);
  });

  it("실제 파일(EventLineageLookupPanel.tsx)의 조회 버튼 문구에 'AI 요약'을 끼워 넣으면 같은 스캔이 red로 뒤집힌다", () => {
    const file = join(SCAN_DIR, "EventLineageLookupPanel.tsx");
    const source = readFileSync(file, "utf-8");
    expect(findInferenceViolations(source)).toHaveLength(0); // 현재는 위반 없음(green)

    const regressed = source.replace(
      '{t("decisions.eventLineage.heading")}',
      '{t("decisions.eventLineage.heading")} (AI 요약)',
    );
    expect(regressed).not.toBe(source); // 치환이 실제로 일어났는지 확인
    expect(findInferenceViolations(regressed).length).toBeGreaterThan(0); // red 재현
  });
});
