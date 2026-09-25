import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { perfBudgetMs } from "./perfBudget";

// task-2705 UX-21: 반응형 브레이크포인트 전 화면 점검 + 모바일 레이아웃 회귀.
// "화면 하나씩 손으로 확인"은 44개 nav 화면 규모에서 유지되지 않으므로,
// errorSurface.guard.test.ts/navReachability.test.ts와 같은 방식으로 정적
// 스캐너를 CI 게이트로 만든다: <table>이 `overflow-x-auto` 래퍼 없이 렌더되면
// 모바일 폭에서 표가 페이지 전체를 가로로 밀어내는 회귀가 나므로 0건을 강제한다.
const SRC_DIR = join(dirname(fileURLToPath(import.meta.url)), "..");
const SCAN_DIR_NAMES = ["routes", "components"];
const WRAP_CLASS = "overflow-x-auto";
const LOOKBACK_WINDOW = 400;

function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
}

/** `<table` 오프닝 태그(여러 줄 속성 포함) 중 가장 가까운 조상에 overflow-x-auto가
 * 없거나, 있어도 그 사이에서 이미 `</div>`로 닫힌 경우(형제 관계로 위장) 위반으로 본다. */
export function findUnwrappedTables(source: string): number[] {
  const stripped = stripComments(source);
  const violations: number[] = [];
  const pattern = /<table\b/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(stripped)) !== null) {
    const before = stripped.slice(Math.max(0, match.index - LOOKBACK_WINDOW), match.index);
    const wrapIdx = before.lastIndexOf(WRAP_CLASS);
    const wrapped = wrapIdx !== -1 && !before.slice(wrapIdx).includes("</div>");
    if (!wrapped) violations.push(match.index);
  }
  return violations;
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

describe("findUnwrappedTables — 스캐너 자체 검증", () => {
  it("래핑 없는 <table>은 위반으로 잡힌다", () => {
    expect(findUnwrappedTables("<div><table><tbody></tbody></table></div>")).toHaveLength(1);
  });

  it("overflow-x-auto로 감싼 <table>은 위반이 아니다", () => {
    expect(
      findUnwrappedTables('<div className="overflow-x-auto"><table><tbody></tbody></table></div>'),
    ).toHaveLength(0);
  });

  it("negative: overflow-y-auto만 있는 래퍼는 축이 달라 위반으로 남는다(가로 스크롤이 안 생긴다)", () => {
    expect(
      findUnwrappedTables('<div className="overflow-y-auto"><table><tbody></tbody></table></div>'),
    ).toHaveLength(1);
  });

  it("negative: overflow-x-auto div가 table 이전에 이미 닫혀 있으면(형제 관계 위장) 위반이다", () => {
    const source = '<div className="overflow-x-auto"></div><table><tbody></tbody></table>';
    expect(findUnwrappedTables(source)).toHaveLength(1);
  });

  it("negative: 여러 줄에 걸친 <table\\n  className=...> 오프닝 태그 경계도 정확히 잡는다", () => {
    const wrapped = '<div className="overflow-x-auto">\n  <table\n    className="w-full">\n  </table>\n</div>';
    expect(findUnwrappedTables(wrapped)).toHaveLength(0);

    const unwrapped = '<div>\n  <table\n    className="w-full">\n  </table>\n</div>';
    expect(findUnwrappedTables(unwrapped)).toHaveLength(1);
  });

  it("실패 주입: ReportsPage.tsx가 이 리프 이전에 실제로 갖고 있던 회귀 스니펫을 재현하면 스캐너가 잡아낸다", () => {
    // task-2705 이전 실제 소스(git 이력) — <table>이 어떤 overflow 래퍼도 없이
    // Card 바로 아래에 렌더됐다. 합성 문자열이 아니라 실제로 존재했던 회귀를
    // 그대로 복원해, 스캐너가 "이론상 잡는다"가 아니라 "실제로 이걸 잡았을
    // 것이다"를 증명한다.
    const beforeFixSnippet = `
      <Card>
        <CardTitle>{t("legacy.reportsPage.t9")}</CardTitle>
        {report.strategyContributions.length > 0 ? (
          <table className="w-full text-sm">
            <thead className="text-left text-fg-muted">
              <tr>
                <th className="pb-2 font-normal">{t("legacy.reportsPage.t10")}</th>
              </tr>
            </thead>
          </table>
        ) : (
          <EmptyState>{t("legacy.reportsPage.t13")}</EmptyState>
        )}
      </Card>
    `;
    expect(findUnwrappedTables(beforeFixSnippet)).toHaveLength(1);
  });

  it("항상 빈 배열을 돌려주는 무력화된 스캐너는 이 테스트로 걸러진다", () => {
    const alwaysEmptyScanner = () => [] as number[];
    const violatingSource = "<table><tbody></tbody></table>";
    expect(findUnwrappedTables(violatingSource).length).toBeGreaterThan(0);
    expect(findUnwrappedTables(violatingSource)).not.toEqual(alwaysEmptyScanner());
  });

  it("perf: 표 500개짜리 합성 소스를 스캔해도 예산 안에 끝난다(정규식 파국적 백트래킹 없음)", () => {
    const repeated = Array.from({ length: 500 }, (_, i) =>
      i % 2 === 0
        ? '<div className="overflow-x-auto"><table><tbody></tbody></table></div>'
        : "<div><table><tbody></tbody></table></div>",
    ).join("\n");

    const start = performance.now();
    const violations = findUnwrappedTables(repeated);
    const elapsed = performance.now() - start;

    expect(violations).toHaveLength(250);
    expect(elapsed).toBeLessThan(perfBudgetMs(200));
  });
});

describe("게이트 적색 재현 — 실제 파일 스캔", () => {
  it("routes/**/*.tsx·components/**/*.tsx 전체에 래핑 없는 <table>이 0건이다(green)", () => {
    const violations: string[] = [];
    for (const dirName of SCAN_DIR_NAMES) {
      const dir = join(SRC_DIR, dirName);
      for (const file of listTsxFiles(dir)) {
        const source = readFileSync(file, "utf-8");
        const found = findUnwrappedTables(source);
        if (found.length > 0) {
          violations.push(`${relative(SRC_DIR, file).replace(/\\/g, "/")}: ${found.length}건`);
        }
      }
    }
    expect(violations).toEqual([]);
  });

  it("실제로 고친 파일(ReadinessChecksTable.tsx)에서 wrapper div를 벗겨내면 같은 스캔이 red로 뒤집힌다", () => {
    const file = join(SRC_DIR, "components", "ReadinessChecksTable.tsx");
    const source = readFileSync(file, "utf-8");
    expect(findUnwrappedTables(source)).toHaveLength(0); // 현재는 고쳐진 상태(green)

    const regressed = source.replace(/<div className="overflow-x-auto">\s*\n(\s*)<table/, "$1<table");
    expect(regressed).not.toBe(source); // 치환이 실제로 일어났는지 확인(포맷이 바뀌면 이 assert가 먼저 잡는다)
    expect(findUnwrappedTables(regressed).length).toBeGreaterThan(0); // red 재현
  });
});
