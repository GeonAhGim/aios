import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// task-1048: task-901/902/910/911/929/930이 일곱 배치에 걸쳐 화면의 에러 표시를
// routeApiError(§3.3 taxonomy 단일 진입점) + ErrorMessage(getApiErrorMessage 매핑)
// 경유로 이관해 놓은 상태를 굳히는 회귀 가드. apiPaths.clientsScan.test.ts(task-1040)와
// 같은 방식으로 vitest에서 fs로 routes/**/*.tsx 소스를 읽어 정규식으로 스캔한다 —
// 새 분류기·새 에러 컴포넌트를 만들지 않고 런타임 동작도 바꾸지 않는다.
const ROUTES_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "routes");

// 파일별로 예외가 필요해지면 "상대경로:매치문자열" 형태로 이유 한 줄과 함께 여기 추가한다.
// 위반이 나왔다고 이 목록을 넓혀 무마하지 말 것 — ErrorMessage로 교체하는 게 기본값이다.
// 현재는 스캔 대상 0건이라 비어 있다.
const ALLOWED_DIRECT_RENDERS = new Set<string>();

function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
}

// error/err(체이닝·(x as Error) 캐스팅 포함)의 .message 접근만 잡는다 — data.message처럼
// 에러가 아닌 응답 필드의 .message는 대상이 아니다(예: strategy preview 미리보기 문구).
const MESSAGE_ACCESS_PATTERN = /(?:[\w$]+\.)*?(?:err|error)(?:\s+as\s+Error)?\)?\.message\b/g;

// 두 가지는 이미 승인된 경로이므로 위반이 아니다:
//  1) ErrorMessage의 message= prop 값으로 넘기는 경우(컴포넌트가 getApiErrorMessage로
//     다시 매핑하지 직접 렌더하지 않는다) — 같은 태그 안, 즉 직전 "message="가 직전
//     ">"보다 뒤에 있으면 prop 값이다.
//  2) getApiErrorMessage(...) 호출 인자로 넘기는 경우 — 호출이 아직 안 닫힌 채 매치가
//     나오면 인자다.
function isApprovedContext(before: string): boolean {
  const lastMessageProp = before.lastIndexOf("message=");
  const lastTagClose = before.lastIndexOf(">");
  if (lastMessageProp !== -1 && lastMessageProp > lastTagClose) return true;

  const lastMapperCall = before.lastIndexOf("getApiErrorMessage(");
  if (lastMapperCall !== -1 && !before.slice(lastMapperCall).includes(")")) return true;

  return false;
}

function findDirectErrorMessageRenders(source: string): string[] {
  const stripped = stripComments(source);
  const violations: string[] = [];
  let match: RegExpExecArray | null;
  MESSAGE_ACCESS_PATTERN.lastIndex = 0;
  while ((match = MESSAGE_ACCESS_PATTERN.exec(stripped)) !== null) {
    const before = stripped.slice(Math.max(0, match.index - 200), match.index);
    if (!isApprovedContext(before)) {
      violations.push(match[0]);
    }
  }
  return violations;
}

function listRouteSourceFiles(dir: string): string[] {
  const files: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      files.push(...listRouteSourceFiles(full));
    } else if (name.endsWith(".tsx") && !name.endsWith(".test.tsx")) {
      files.push(full);
    }
  }
  return files;
}

describe("routes — err.message 직접 렌더 0건 회귀 가드(task-1048)", () => {
  it("findDirectErrorMessageRenders는 위반을 인위로 넣으면 잡아낸다(스캐너 자체 검증)", () => {
    expect(
      findDirectErrorMessageRenders('<p>{(start.error as Error).message}</p>'),
    ).toHaveLength(1);
    expect(findDirectErrorMessageRenders('// {(err as Error).message}')).toHaveLength(0);
  });

  it("승인된 경로(ErrorMessage의 message prop, getApiErrorMessage 인자)는 위반이 아니다", () => {
    expect(
      findDirectErrorMessageRenders(
        '<ErrorMessage message={error instanceof Error ? error.message : undefined} />',
      ),
    ).toHaveLength(0);
    expect(findDirectErrorMessageRenders("getApiErrorMessage(err.errorCode, err.message)")).toHaveLength(
      0,
    );
  });

  it("routes/**/*.tsx 소스에 err.message/error.message 직접 렌더가 0건이다(allow-list 제외)", () => {
    const violations: string[] = [];
    for (const file of listRouteSourceFiles(ROUTES_DIR)) {
      const relPath = relative(ROUTES_DIR, file).replace(/\\/g, "/");
      const source = readFileSync(file, "utf-8");
      for (const literal of findDirectErrorMessageRenders(source)) {
        if (!ALLOWED_DIRECT_RENDERS.has(`${relPath}:${literal}`)) {
          violations.push(`${relPath}: "${literal}"`);
        }
      }
    }
    expect(violations).toEqual([]);
  });
});
