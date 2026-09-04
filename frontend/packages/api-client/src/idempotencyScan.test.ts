import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { API_ROUTES, type ApiRouteName } from "./apiPaths";

// task-1333: §3.7 IdempotencyScope · §9 PLT-15 전수 회귀 가드. apiPaths.ts의
// idempotencyRequired 표식(단일 출처)이 실제 호출부와 어긋나지 않는지 소스
// 스캔으로 양방향 대조한다. 개별 클라이언트 테스트(idempotency.test.ts 등,
// task-321/338/493/1024/1049)는 "헤더가 붙는지"만 보장할 뿐, "새 금전 라우트에
// 부착을 빠뜨렸는지"는 아무도 보지 않았다 — 이 파일이 그 틈을 막는다.
const CLIENTS_DIR = join(dirname(fileURLToPath(import.meta.url)), "clients");

const IDEMPOTENT_METHODS = new Set(["postIdempotent", "postEnvelopeIdempotent"]);

// route()의 주석대로 legacyPath는 "리소스 경로"의 단일 출처이지 "연산"의 단일
// 출처가 아니다 — GET 목록·POST 생성이 같은 라우트 이름을 공유한다(예:
// executions.base = listExecutions의 requestByRoute + createExecution의
// postIdempotent). requestByRoute/request/requestEnvelope는 이 레포에서 항상
// GET 조회 용도로만 쓰이므로(POST 바디를 싣는 money 연산은 전부 post 계열
// 헬퍼를 거친다), "금전 라우트가 비멱등으로 호출됐다" 위반은 실제로 post 계열
// 뮤테이션 메서드로 대체됐을 때만 의미가 있다.
const MUTATING_NON_IDEMPOTENT_METHODS = new Set([
  "post",
  "postEnvelope",
  "put",
  "putEnvelope",
  "patch",
  "patchEnvelope",
  "del",
]);

// 주석 안의 코드 예시(설명용)가 실호출로 오탐되지 않도록 지운다. 길이·개행은
// 보존해 이후 정규식의 인덱스 기반 탐색(findConsumingMethod)이 흔들리지 않게 한다.
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "))
    .replace(/\/\/[^\n]*/g, (m) => " ".repeat(m.length));
}

interface RouteCallSite {
  routeName: string;
  method: string;
}

// foundation.ts의 PAPER_DEPLOYMENT_COMMAND_ROUTES처럼 `Record<Command, ApiRouteName>`
// 꼴로 라우트 이름을 간접 참조하는 모듈 상수를 찾아 식별자→라우트이름[] 로 펼친다.
function extractRouteMapIdentifiers(source: string): Map<string, string[]> {
  const map = new Map<string, string[]>();
  const pattern = /const\s+([A-Za-z_$][\w$]*)\s*:\s*Record<[^=]*>\s*=\s*\{([\s\S]*?)\n\};/g;
  let m: RegExpExecArray | null;
  while ((m = pattern.exec(source)) !== null) {
    const routeNames = [...m[2].matchAll(/"([a-zA-Z][\w]*(?:\.[a-zA-Z][\w]*)+)"/g)].map((x) => x[1]);
    map.set(m[1], routeNames);
  }
  return map;
}

// `const path = resolvePath(...)...; ... this.<method>(path, ...)` 처럼 라우트가
// 변수를 거쳐 호출부에 도달하는 경우, 대입 지점 이후 가장 가까운 소비 호출을 찾는다.
// 윈도우를 두는 이유: 같은 변수명(`path`)이 다른 메서드에서도 재사용되므로 무한정
// 탐색하면 엉뚱한 메서드의 호출을 집어올 수 있다 — 이 레포의 실제 스타일(대입 직후
// 바로 소비)에서는 600자면 충분하고 넘치는 법이 없다.
function findConsumingMethod(source: string, fromIndex: number, varName: string): string | null {
  const window = source.slice(fromIndex, fromIndex + 600);
  const match = window.match(new RegExp(`this\\.([A-Za-z]\\w*)\\(\\s*${varName}\\b`));
  return match ? match[1] : null;
}

// clients/*.ts 소스 하나에서 "이 라우트가 어떤 this.<method>(...)로 호출됐는지"
// 전부 뽑아낸다. 패턴은 실제 코드에서 관찰되는 4가지뿐이다:
//   A) this.<method>(resolvePath("route")...)            — 직접 중첩
//   A') this.<method>(resolvePath(IDENT)...)              — Record 간접 참조 직접 중첩
//   B) const v = resolvePath("route")...; this.<method>(v)  — 변수 경유
//   B') const v = resolvePath(IDENT)...; this.<method>(v)   — Record 간접 참조 변수 경유
//   C) this.requestByRoute("route"...)                    — resolvePath 없이 직접
export function findCallSites(source: string): RouteCallSite[] {
  const stripped = stripComments(source);
  const routeMaps = extractRouteMapIdentifiers(stripped);
  const sites: RouteCallSite[] = [];
  let m: RegExpExecArray | null;

  const directLiteral = /this\.([A-Za-z]\w*)\(\s*resolvePath\(\s*"([a-zA-Z][\w.]*)"/g;
  while ((m = directLiteral.exec(stripped)) !== null) {
    sites.push({ method: m[1], routeName: m[2] });
  }

  const directIdent = /this\.([A-Za-z]\w*)\(\s*resolvePath\(\s*([A-Za-z_$][\w$]*)\s*[[)]/g;
  while ((m = directIdent.exec(stripped)) !== null) {
    for (const routeName of routeMaps.get(m[2]) ?? []) sites.push({ method: m[1], routeName });
  }

  const varLiteral = /const\s+(\w+)\s*=\s*resolvePath\(\s*"([a-zA-Z][\w.]*)"/g;
  while ((m = varLiteral.exec(stripped)) !== null) {
    const method = findConsumingMethod(stripped, m.index + m[0].length, m[1]);
    if (method) sites.push({ method, routeName: m[2] });
  }

  const varIdent = /const\s+(\w+)\s*=\s*resolvePath\(\s*([A-Za-z_$][\w$]*)\s*[[)]/g;
  while ((m = varIdent.exec(stripped)) !== null) {
    const method = findConsumingMethod(stripped, m.index + m[0].length, m[1]);
    if (method) for (const routeName of routeMaps.get(m[2]) ?? []) sites.push({ method, routeName });
  }

  const byRoute = /this\.(requestByRoute)\(\s*"([a-zA-Z][\w.]*)"/g;
  while ((m = byRoute.exec(stripped)) !== null) {
    sites.push({ method: m[1], routeName: m[2] });
  }

  return sites;
}

interface ScanResult {
  // 표식은 있는데(idempotencyRequired=true) 멱등 호출부가 하나도 없다.
  markedWithoutIdempotentCall: string[];
  // 표식은 있는데 일반(비멱등) 메서드로 호출됐다.
  markedButNonIdempotentCall: string[];
  // 멱등 메서드로 호출됐는데 표식이 없다.
  idempotentCallButUnmarked: string[];
}

// DoD (2)(3): 소스 스캔 + 표식 양방향 대조. 화이트리스트는 두지 않는다 — 이 레포의
// 실제 14개 금전 라우트가 전부 근거(주석)와 함께 apiPaths.ts에 등록돼 있으므로
// 예외를 둘 이유가 없다.
export function scanCallSites(
  files: Array<{ path: string; source: string }>,
  routes: Record<string, { idempotencyRequired?: boolean }>,
): ScanResult {
  const moneyRoutes = new Set(Object.entries(routes).filter(([, d]) => d.idempotencyRequired).map(([name]) => name));
  const seenIdempotentForRoute = new Set<string>();
  const markedButNonIdempotentCall: string[] = [];
  const idempotentCallButUnmarked: string[] = [];

  for (const file of files) {
    for (const site of findCallSites(file.source)) {
      const isIdempotentMethod = IDEMPOTENT_METHODS.has(site.method);
      const isMoney = moneyRoutes.has(site.routeName);
      if (isMoney && isIdempotentMethod) seenIdempotentForRoute.add(site.routeName);
      if (isMoney && MUTATING_NON_IDEMPOTENT_METHODS.has(site.method)) {
        markedButNonIdempotentCall.push(`${file.path}: "${site.routeName}" via this.${site.method}(...)`);
      }
      if (!isMoney && isIdempotentMethod) {
        idempotentCallButUnmarked.push(`${file.path}: "${site.routeName}" via this.${site.method}(...)`);
      }
    }
  }

  const markedWithoutIdempotentCall = [...moneyRoutes].filter((r) => !seenIdempotentForRoute.has(r));
  return { markedWithoutIdempotentCall, markedButNonIdempotentCall, idempotentCallButUnmarked };
}

function listClientSourceFiles(): Array<{ path: string; source: string }> {
  return readdirSync(CLIENTS_DIR)
    .filter((name) => name.endsWith(".ts") && !name.endsWith(".test.ts"))
    .map((name) => ({ path: `clients/${name}`, source: readFileSync(join(CLIENTS_DIR, name), "utf-8") }));
}

describe("idempotencyScan — findCallSites 자체 검증(스캐너 파서)", () => {
  it("패턴 A(직접 중첩)를 인식한다", () => {
    expect(findCallSites('return this.postIdempotent(resolvePath("x.y"), body, key);')).toEqual([
      { method: "postIdempotent", routeName: "x.y" },
    ]);
  });

  it("패턴 B(변수 경유)를 인식한다", () => {
    const src = 'const path = resolvePath("x.y").replace(":id", "1");\nreturn this.postIdempotent(path, body, key);';
    expect(findCallSites(src)).toEqual([{ method: "postIdempotent", routeName: "x.y" }]);
  });

  it("Record 간접 참조(변수 경유)를 여러 라우트로 펼친다", () => {
    const src = [
      'const CMD: Record<string, string> = {',
      '  start: "x.start",',
      '  stop: "x.stop",',
      "};",
      "const path = resolvePath(CMD[command]).replace(':id', d);",
      "return this.postEnvelopeIdempotent(path, body, key);",
    ].join("\n");
    expect(findCallSites(src)).toEqual([
      { method: "postEnvelopeIdempotent", routeName: "x.start" },
      { method: "postEnvelopeIdempotent", routeName: "x.stop" },
    ]);
  });

  it("requestByRoute 직접 호출을 인식한다", () => {
    expect(findCallSites('return this.requestByRoute("x.y");')).toEqual([
      { method: "requestByRoute", routeName: "x.y" },
    ]);
  });

  it("주석 속 예시 코드는 무시한다", () => {
    expect(findCallSites('// return this.postIdempotent(resolvePath("x.y"), body, key);')).toEqual([]);
  });
});

describe("idempotencyScan — negative fixture(위반이 실제로 FAIL한다)", () => {
  const MONEY_ROUTES = { "x.money": { idempotencyRequired: true } };

  it("금전 라우트를 일반 post로 바꾼 fixture는 markedButNonIdempotentCall·markedWithoutIdempotentCall을 채운다", () => {
    const files = [{ path: "fixture.ts", source: 'return this.post(resolvePath("x.money"), body);' }];
    const result = scanCallSites(files, MONEY_ROUTES);
    expect(result.markedButNonIdempotentCall).toEqual(['fixture.ts: "x.money" via this.post(...)']);
    expect(result.markedWithoutIdempotentCall).toEqual(["x.money"]);
  });

  it("표식만 지운 fixture(멱등 호출은 그대로)는 idempotentCallButUnmarked를 채운다", () => {
    const files = [{ path: "fixture.ts", source: 'return this.postIdempotent(resolvePath("x.money"), body, key);' }];
    const result = scanCallSites(files, { "x.money": { idempotencyRequired: false } });
    expect(result.idempotentCallButUnmarked).toEqual(['fixture.ts: "x.money" via this.postIdempotent(...)']);
  });

  it("정상 fixture(표식+멱등 호출 일치)는 세 목록 모두 비어 있다", () => {
    const files = [{ path: "fixture.ts", source: 'return this.postIdempotent(resolvePath("x.money"), body, key);' }];
    const result = scanCallSites(files, MONEY_ROUTES);
    expect(result.markedWithoutIdempotentCall).toEqual([]);
    expect(result.markedButNonIdempotentCall).toEqual([]);
    expect(result.idempotentCallButUnmarked).toEqual([]);
  });
});

// DoD (2)(3): 실제 clients/*.ts 전수 스캔. 이 테스트가 새 금전 라우트의 멱등
// 부착 누락(또는 표식 누락)을 잡는 회귀 가드 본체다.
describe("idempotencyScan — clients/*.ts 전수 스캔(회귀 가드 본체)", () => {
  it("apiPaths.ts 표식과 실제 호출부가 완전히 일치한다(양방향)", () => {
    const result = scanCallSites(listClientSourceFiles(), API_ROUTES);
    expect(result.markedWithoutIdempotentCall).toEqual([]);
    expect(result.markedButNonIdempotentCall).toEqual([]);
    expect(result.idempotentCallButUnmarked).toEqual([]);
  });
});

// spec §9 PLT-15 원문(라인 438) 전체 목록을 이 레포의 실제 라우트 이름으로 옮긴
// 것 — apiPaths.ts의 idempotencyRequired 표식과 정확히 일치해야 한다(양방향
// 대조). "admin confirm-payment"는 spec 산문 표현이고, src/api/routers/admin.py
// 원본에는 별도 라우트 없이 wallet/topups/{id}/confirm 하나뿐이다(task-1333 확인).
const PLT15_MONEY_ROUTES: readonly ApiRouteName[] = [
  "marketplace.listings.purchase",
  "admin.wallet.topupConfirm",
  "wallet.topupRequests",
  "executions.base",
  "executions.start",
  "executions.convertToLive",
  "portfolio.rebalance",
  "exchange.credentials.base",
  "foundation.paperDeployments.request",
  "foundation.paperDeployments.start",
  "foundation.paperDeployments.resume",
  "foundation.paperDeployments.pause",
  "foundation.paperDeployments.stop",
  "foundation.trustConsents.accept",
];

describe("idempotencyScan — PLT-15 라우트 목록 양방향 대조", () => {
  it("PLT15_MONEY_ROUTES 각 항목은 API_ROUTES에서 idempotencyRequired=true다", () => {
    for (const name of PLT15_MONEY_ROUTES) {
      expect(API_ROUTES[name].idempotencyRequired).toBe(true);
    }
  });

  it("API_ROUTES에서 idempotencyRequired=true인 라우트는 PLT15_MONEY_ROUTES와 정확히 일치한다", () => {
    const marked = Object.entries(API_ROUTES)
      .filter(([, def]) => def.idempotencyRequired)
      .map(([name]) => name)
      .sort();
    expect(marked).toEqual([...PLT15_MONEY_ROUTES].sort());
  });
});
