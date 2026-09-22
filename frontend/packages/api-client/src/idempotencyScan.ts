import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { readFileSync, readdirSync } from "node:fs";
import ts from "typescript";

// task-1333: §3.7 IdempotencyScope · §9 PLT-15 전수 회귀 가드. apiPaths.ts의
// idempotencyRequired 표식(단일 출처)이 실제 호출부와 어긋나지 않는지 소스
// 스캔으로 양방향 대조한다. 개별 클라이언트 테스트(idempotency.test.ts 등,
// task-321/338/493/1024/1049)는 "헤더가 붙는지"만 보장할 뿐, "새 금전 라우트에
// 부착을 빠뜨렸는지"는 아무도 보지 않았다 — 이 모듈이 그 틈을 막는다.
//
// task-4973: idempotencyScan.test.ts에서 스캐너 본체(findCallSites/
// scanCallSites/listClientSourceFiles)를 분리했다. bench/idempotencyScan_bench.mjs가
// 벽시계 성능(ratio ratchet)을, idempotencyScan.test.ts가 기능(결과 집합)을 각각
// 검증하며 둘 다 같은 스캐너 구현을 임포트한다 — 로직이 테스트 파일에만 있으면
// bench가 실제 프로덕션 경로가 아닌 사본을 측정할 위험이 있었다.
export const CLIENTS_DIR = join(dirname(fileURLToPath(import.meta.url)), "clients");

export const IDEMPOTENT_METHODS = new Set(["postIdempotent", "postEnvelopeIdempotent"]);

// route()의 주석대로 legacyPath는 "리소스 경로"의 단일 출처이지 "연산"의 단일
// 출처가 아니다 — GET 목록·POST 생성이 같은 라우트 이름을 공유한다(예:
// executions.base = listExecutions의 requestByRoute + createExecution의
// postIdempotent). requestByRoute/request/requestEnvelope는 이 레포에서 항상
// GET 조회 용도로만 쓰이므로(POST 바디를 싣는 money 연산은 전부 post 계열
// 헬퍼를 거친다), "금전 라우트가 비멱등으로 호출됐다" 위반은 실제로 post 계열
// 뮤테이션 메서드로 대체됐을 때만 의미가 있다.
export const MUTATING_NON_IDEMPOTENT_METHODS = new Set([
  "post",
  "postEnvelope",
  "put",
  "putEnvelope",
  "patch",
  "patchEnvelope",
  "del",
]);

// 주석 안의 코드 예시(설명용)가 실호출로 오탐되지 않도록 지운다. 길이·개행은
// 보존해 이후 정규식의 인덱스 기반 탐색(consumingMethods)이 흔들리지 않게 한다.
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "))
    .replace(/\/\/[^\n]*/g, (m) => " ".repeat(m.length));
}

export interface RouteCallSite {
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

// 각 변수의 심볼로 소비 호출을 묶어 재사용은 모두 수집하고 다른 스코프의
// 동명 변수는 제외한다. 타입 검사·파일 읽기 없이 현재 소스 하나만 바인딩한다.
function consumingMethods(source: string): Map<number, string[]> {
  const file = ts.createSourceFile("scan.ts", source, ts.ScriptTarget.Latest, true);
  const options = { noLib: true, noResolve: true };
  const host = ts.createCompilerHost(options);
  host.getSourceFile = (name) => name === "scan.ts" ? file : undefined;
  const checker = ts.createProgram(["scan.ts"], options, host).getTypeChecker();
  const methods = new Map<number, string[]>();
  const visit = (node: ts.Node) => {
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
      && node.expression.expression.kind === ts.SyntaxKind.ThisKeyword) {
      const argument = node.arguments[0];
      if (argument && ts.isIdentifier(argument)) {
        const declaration = checker.getSymbolAtLocation(argument)?.valueDeclaration;
        if (declaration && ts.isVariableDeclaration(declaration)) {
          const position = declaration.getStart(file);
          const consumers = methods.get(position) ?? [];
          consumers.push(node.expression.name.text);
          methods.set(position, consumers);
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return methods;
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
  const consumers = consumingMethods(stripped);
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
    for (const method of consumers.get(m.index + m[0].indexOf(m[1], 5)) ?? []) {
      sites.push({ method, routeName: m[2] });
    }
  }

  const varIdent = /const\s+(\w+)\s*=\s*resolvePath\(\s*([A-Za-z_$][\w$]*)\s*[[)]/g;
  while ((m = varIdent.exec(stripped)) !== null) {
    for (const method of consumers.get(m.index + m[0].indexOf(m[1], 5)) ?? []) {
      for (const routeName of routeMaps.get(m[2]) ?? []) sites.push({ method, routeName });
    }
  }

  const byRoute = /this\.(requestByRoute)\(\s*"([a-zA-Z][\w.]*)"/g;
  while ((m = byRoute.exec(stripped)) !== null) {
    sites.push({ method: m[1], routeName: m[2] });
  }

  return sites;
}

export interface ScanResult {
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

export function listClientSourceFiles(): Array<{ path: string; source: string }> {
  return readdirSync(CLIENTS_DIR)
    .filter((name) => name.endsWith(".ts") && !name.endsWith(".test.ts"))
    .map((name) => ({ path: `clients/${name}`, source: readFileSync(join(CLIENTS_DIR, name), "utf-8") }));
}
