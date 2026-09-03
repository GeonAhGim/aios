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
