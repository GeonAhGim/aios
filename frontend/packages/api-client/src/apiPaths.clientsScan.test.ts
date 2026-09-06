import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// task-1040: task-840/942/1023이 세 배치에 걸쳐 clients/*.ts를 resolvePath(route) 경유로
// 옮겨 놓은 상태를 굳히는 회귀 가드. apiPaths.test.ts에 합치면 300줄을 넘어서므로
// 별도 파일로 둔다. 새 클라이언트 메서드를 추가하면서 다시 "/foo/bar" 같은 문자열을
// 직접 fetch 계열에 박아 넣는 실수를 소스 스캔으로 잡는다(런타임 동작 변경 없음).
const CLIENTS_DIR = join(dirname(fileURLToPath(import.meta.url)), "clients");

// platform.ts의 "/readyz"(그리고 아직 클라이언트가 없는 "/livez"·"/metrics")는 spec
// §3.2/§9 PLT-09 인프라 프로브다 — 봉투 미적용 + /api/v1 버저닝 대상도 아니라서
// API_ROUTES 등록 대상에서 영구 제외된 채 직접 호출된다(apiPaths.test.ts의
// INFRA_PATHS·task-942 decision과 동일 이유 — 목록을 넓혀 위반을 무마하지 말 것).
const ALLOWED_HARDCODED_PATHS = new Set(["/readyz", "/livez", "/metrics"]);

function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
}

// resolvePath(route)의 route 인자("auth.login" 같은 dot-name)는 "/"로 시작하지
// 않으므로 이 패턴에 걸리지 않는다 — 여기서 잡는 건 실제 URL 경로 리터럴뿐이다.
function findPathLikeStringLiterals(source: string): string[] {
  const pattern = /["'`](\/[a-zA-Z0-9_\-:/.]*)["'`]/g;
  const found: string[] = [];
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(stripComments(source))) !== null) {
    found.push(match[1]);
  }
  return found;
}

function listClientSourceFiles(): string[] {
  return readdirSync(CLIENTS_DIR).filter((name) => name.endsWith(".ts") && !name.endsWith(".test.ts"));
}

describe("apiPaths — clients/*.ts 하드코딩 경로 소스 스캔 회귀 가드(task-1040)", () => {
  it("findPathLikeStringLiterals는 위반 문자열을 인위로 넣으면 잡아낸다(스캐너 자체 검증)", () => {
    expect(findPathLikeStringLiterals('return this.request("/hardcoded/path");')).toContain("/hardcoded/path");
    expect(findPathLikeStringLiterals('// return this.request("/hardcoded/path");')).not.toContain(
      "/hardcoded/path",
    );
  });

  it("clients/*.ts 소스에 남은 하드코딩 경로 문자열이 0건이다(INFRA_PATHS 제외)", () => {
    const violations: string[] = [];
    for (const file of listClientSourceFiles()) {
      const source = readFileSync(join(CLIENTS_DIR, file), "utf-8");
      for (const literal of findPathLikeStringLiterals(source)) {
        if (!ALLOWED_HARDCODED_PATHS.has(literal)) {
          violations.push(`${file}: "${literal}"`);
        }
      }
    }
    expect(violations).toEqual([]);
  });
});

// task-1160: 배치1(task-1159)이 account/admin/auth/exchange를 requestByRoute·
// resolveEnvelope(route) 관용으로 옮긴 뒤, 배치2가 남은 clients/*.ts(executions/
// marketplace/notifications/portfolio/strategyBuilder/marketData)도 같은 관용으로
// 옮겨 놓은 상태를 굳히는 회귀 가드다. this.request()/this.requestEnvelope()를
// 직접 호출하면서 apiPaths.ts registry(resolveEnvelope)를 거치지 않는 형태가 다시
// 생기지 않는지를 잡는다 — resolvePath(...)를 곧바로 인라인으로 넘기는 형태
// (`this.request(resolvePath(...))`)뿐 아니라, marketData.ts의 실제 이전 버그처럼
// 경로를 변수에 먼저 담아(`const path = resolvePath(...)`) 그 변수를 등록값과 무관하게
// 무조건 request()/requestEnvelope() 한쪽에만 넘기는 형태도 하드코딩이다 — 두 형태
// 모두 "이 라우트는 항상 봉투(또는 항상 legacy)"라고 호출부가 스스로 단정하는
// 것이므로 레지스트리 값이 바뀌어도 반영되지 않는다. requestByRoute(route) 또는
// `resolveEnvelope(route) ? requestEnvelope(path) : request(path)`(경로 치환·쿼리가
// 있어 requestByRoute를 못 쓰는 경우) 삼항만 위반이 아니다 — 최종 분기가 레지스트리
// 값을 거치는 유일한 두 형태다. 삼항 안에서 호출되는 request/requestEnvelope는
// "커버된 범위"로 표시해 두고, 그 범위 밖에서 발견되는 모든 this.request(/
// this.requestEnvelope( 호출을 위반으로 잡는다(제네릭 인자 `<T>`, await, 개행 허용).
const GATED_TERNARY_PATTERN =
  /resolveEnvelope\([^)]*\)\s*\?\s*(?:await\s+)?this\.requestEnvelope(?:<[^>]*>)?\([^)]*\)\s*:\s*(?:await\s+)?this\.request(?:<[^>]*>)?\([^)]*\)/g;
// positions.ts(task-1377/1524)의 fetchByRoute처럼 삼항 대신 if(resolveEnvelope) {
// ...requestEnvelopeWithMeta...return...} return ...this.request(...) 형태(조기
// return)로 분기하는 관용도 있다 — 이 파일이 다루는 clients/*.ts 전체를 스캔하므로
// task-1160 대상 6개 파일 밖의 이 기존 관용까지 오탐하지 않게 함께 인정한다.
const GATED_IF_FALLTHROUGH_PATTERN =
  /if\s*\(\s*resolveEnvelope\([^)]*\)\s*\)\s*\{[\s\S]*?this\.requestEnvelope(?:WithMeta)?(?:<[^>]*>)?\([^)]*\)[\s\S]*?\}\s*return[\s\S]*?this\.request(?:<[^>]*>)?\([^)]*\)/g;
const ENVELOPE_CALL_PATTERN = /this\.(?:requestEnvelope(?:WithMeta)?|request)(?:<[^>]*>)?\(/g;

function findHardcodedEnvelopeBranches(source: string): string[] {
  const stripped = stripComments(source);
  const gatedRanges: Array<[number, number]> = [];
  let ternaryMatch: RegExpExecArray | null;
  while ((ternaryMatch = GATED_TERNARY_PATTERN.exec(stripped)) !== null) {
    gatedRanges.push([ternaryMatch.index, ternaryMatch.index + ternaryMatch[0].length]);
  }
  let ifMatch: RegExpExecArray | null;
  while ((ifMatch = GATED_IF_FALLTHROUGH_PATTERN.exec(stripped)) !== null) {
    gatedRanges.push([ifMatch.index, ifMatch.index + ifMatch[0].length]);
  }
  const found: string[] = [];
  let callMatch: RegExpExecArray | null;
  while ((callMatch = ENVELOPE_CALL_PATTERN.exec(stripped)) !== null) {
    const isGated = gatedRanges.some(([start, end]) => callMatch!.index >= start && callMatch!.index < end);
    if (!isGated) {
      found.push(callMatch[0]);
    }
  }
  return found;
}

describe("apiPaths — clients/*.ts 봉투 분기 하드코딩 소스 스캔 회귀 가드(task-1160)", () => {
  it("findHardcodedEnvelopeBranches는 위반 코드를 인위로 넣으면 잡아낸다(스캐너 자체 검증)", () => {
    expect(findHardcodedEnvelopeBranches('return this.request(resolvePath("x.y"));')).toHaveLength(1);
    expect(
      findHardcodedEnvelopeBranches('return this.requestEnvelope(resolvePath("x.y").replace(":a", "1"));'),
    ).toHaveLength(1);
    expect(
      findHardcodedEnvelopeBranches('return this.request(\n  resolvePath("x.y"),\n);'),
    ).toHaveLength(1);
    expect(findHardcodedEnvelopeBranches('// return this.request(resolvePath("x.y"));')).toHaveLength(0);
  });

  it("marketData.ts의 실제 이전 버그(변수에 담은 경로를 무조건 한쪽에만 넘기는 형태)도 잡아낸다", () => {
    expect(
      findHardcodedEnvelopeBranches(
        'const path = this.withQuery(resolvePath("marketData.instruments.list"), query);\n' +
          "const raw = keysToSnake(await this.request<unknown>(path));",
      ),
    ).toHaveLength(1);
  });

  it("requestByRoute·resolveEnvelope 삼항 관용은 위반이 아니다", () => {
    expect(findHardcodedEnvelopeBranches('return this.requestByRoute("x.y");')).toHaveLength(0);
    expect(
      findHardcodedEnvelopeBranches(
        'const path = resolvePath("x.y").replace(":a", "1");\n' +
          "return resolveEnvelope(\"x.y\") ? this.requestEnvelope(path) : this.request(path);",
      ),
    ).toHaveLength(0);
    expect(
      findHardcodedEnvelopeBranches(
        'const raw = resolveEnvelope(route)\n' +
          "  ? await this.requestEnvelope<unknown>(path)\n" +
          "  : await this.request<unknown>(path);",
      ),
    ).toHaveLength(0);
    expect(
      findHardcodedEnvelopeBranches(
        "return resolveEnvelope(\"strategyBuilder.indicators.compute\")\n" +
          "  ? this.requestEnvelope(path)\n" +
          "  : this.request(path);",
      ),
    ).toHaveLength(0);
  });

  it("clients/*.ts 소스에 봉투 분기를 하드코딩한 호출부가 0건이다", () => {
    const violations: string[] = [];
    for (const file of listClientSourceFiles()) {
      const source = readFileSync(join(CLIENTS_DIR, file), "utf-8");
      for (const literal of findHardcodedEnvelopeBranches(source)) {
        violations.push(`${file}: "${literal}"`);
      }
    }
    expect(violations).toEqual([]);
  });
});
