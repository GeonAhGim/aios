import { describe, expect, it, vi } from "vitest";
import { API_ROUTES, type ApiRouteName } from "./apiPaths";

// [QA task-2730 DEPTH_PLT] 실패 주입: 순수 소스 스캔이라 원래 외부 의존성이 없으나,
// listClientSourceFiles가 거치는 유일한 외부 경계(파일시스템)를 vi.mock으로 대체해
// 읽기 실패를 인위적으로 주입한다. mockImplementationOnce는 1회 호출 후 actual 구현으로
// 자동 복귀하므로 다른 테스트에 영향을 주지 않는다. idempotencyScan.ts가 node:fs를
// import하는 시점보다 이 mock이 먼저 적용되도록 vi.mock은 항상 호이스팅된다.
vi.mock("node:fs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("node:fs")>();
  return { ...actual, readdirSync: vi.fn(actual.readdirSync), readFileSync: vi.fn(actual.readFileSync) };
});
const { readdirSync, readFileSync } = await import("node:fs");

// task-4973: 스캐너 본체(findCallSites/scanCallSites/listClientSourceFiles)는
// idempotencyScan.ts로 옮겼다 — bench/idempotencyScan_bench.mjs(성능, N배 ratchet)와
// 이 파일(기능, 결과 집합)이 같은 구현을 공유한다. 이 파일은 이제 기능 단언만 갖는다.
const { findCallSites, scanCallSites, listClientSourceFiles } = await import("./idempotencyScan");

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

  it.each(["post", "postEnvelope", "put", "patch", "del"])("멱등 호출 뒤 같은 경로의 %s 호출도 검출한다", (method) => {
    for (const indirect of [false, true]) {
      const source = [
        'const CMD: Record<string, string> = {',
        '  start: "x.money",',
        '};',
        `const path = resolvePath(${indirect ? "CMD[command]" : '"x.money"'});`,
        "this.postIdempotent(path, body, key);",
        `this.${method}(path, body);`,
      ].join("\n");
      expect(scanCallSites([{ path: "fixture.ts", source }], MONEY_ROUTES)).toEqual({
        markedWithoutIdempotentCall: [],
        markedButNonIdempotentCall: [`fixture.ts: "x.money" via this.${method}(...)`],
        idempotentCallButUnmarked: [],
      });
    }
  });

  it("다른 메서드의 동명 경로를 이전 라우트의 소비로 오인하지 않는다", () => {
    const source = `class Client {
      money() { const path = resolvePath("x.money"); this.postIdempotent(path, body, key); }
      other() { const path = resolvePath("x.other"); this.post(path, body); }
    }`;
    expect(scanCallSites([{ path: "fixture.ts", source }], MONEY_ROUTES).markedButNonIdempotentCall).toEqual([]);
  });

  it("600자 뒤의 재사용도 수집하고 내부 블록의 동명 변수는 제외한다", () => {
    const source = `const path = resolvePath("x.money");
      this.postIdempotent(path, body, key);
      { const path = resolvePath("x.other"); this.post(path, body); }
      ${" ".repeat(650)}
      this.put(path, body);`;
    expect(scanCallSites([{ path: "fixture.ts", source }], MONEY_ROUTES).markedButNonIdempotentCall)
      .toEqual(['fixture.ts: "x.money" via this.put(...)']);
  });

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

  // [QA task-2730 DEPTH_PLT] negative 3번째: 기존 2건은 모두 패턴 A(직접 리터럴 중첩)만
  // 겨냥했다. foundation.ts의 PAPER_DEPLOYMENT_COMMAND_ROUTES처럼 Record 간접 참조를
  // 거쳐 변수로 소비되는 실제 스타일(패턴 B')에서도 비멱등 재호출이 검출되는지는
  // 아무도 확인하지 않았다 — findCallSites 자체 검증 테스트는 이 조합을 멱등 메서드
  // 경로로만 확인했을 뿐, "위반"(post 등 비멱등)으로는 확인하지 않는다.
  it("Record 간접 참조(변수 경유)로 비멱등 재호출된 fixture도 markedButNonIdempotentCall을 채운다", () => {
    const files = [
      {
        path: "fixture.ts",
        source: [
          'const CMD: Record<string, string> = {',
          '  start: "x.money",',
          "};",
          "const path = resolvePath(CMD[command]).replace(':id', d);",
          "return this.post(path, body);",
        ].join("\n"),
      },
    ];
    const result = scanCallSites(files, MONEY_ROUTES);
    expect(result.markedButNonIdempotentCall).toEqual(['fixture.ts: "x.money" via this.post(...)']);
    expect(result.markedWithoutIdempotentCall).toEqual(["x.money"]);
  });
});

// [QA task-2730 DEPTH_PLT] 실패 주입: listClientSourceFiles가 거치는 유일한 외부 경계인
// 파일시스템 읽기가 깨졌을 때 스캔이 결과를 조용히 비워서 "위반 없음"으로 위장하지
// 않고 즉시 예외를 전파하는지(fail-closed) 확인한다. 이게 없으면 CI 러너의 권한
// 문제 등으로 clients/*.ts 일부가 안 읽혀도 게이트가 녹색으로 통과해버릴 수 있다.
describe("idempotencyScan — 실패 주입(파일시스템 읽기 실패는 조용히 삼켜지지 않는다)", () => {
  it("readFileSync가 던지면 listClientSourceFiles가 그대로 전파한다(fail-closed)", () => {
    vi.mocked(readFileSync).mockImplementationOnce(() => {
      throw new Error("EACCES: permission denied, open 'clients/wallet.ts'");
    });
    expect(() => listClientSourceFiles()).toThrow("EACCES: permission denied");
  });

  it("readdirSync가 던지면 listClientSourceFiles가 그대로 전파한다(fail-closed)", () => {
    vi.mocked(readdirSync).mockImplementationOnce(() => {
      throw new Error("ENOENT: no such file or directory, scandir 'clients'");
    });
    expect(() => listClientSourceFiles()).toThrow("ENOENT: no such file or directory");
  });
});

// task-4973: CI 적색 실제 원인 2/2 — 이 자리에 있던 "clients/*.ts 전수 스캔이 200ms
// 이내"(이후 300~550ms 관측치로 완화해 1000ms까지 올렸던, a22cdb09/task-4968) 벽시계
// 상한 단위 테스트는 설계 결함이었다: CI 워크트리는 `npm run test --workspaces`로 5개
// 워크스페이스의 vitest worker thread가 같은 머신에서 동시에 뜨고,
// ts.createSourceFile(consumingMethods)의 JIT/GC가 그 CPU 경합에 비선형으로 반응해
// 값이 흔들린다 — 완화(상한을 계속 올리는 것)는 금지되어 있고, 애초에 벽시계 절대
// 상한 자체가 공유 하드웨어에서 플레이키하다(같은 이유로 density_bench.mjs도
// 절대 ms 게이트를 쓰지 않는다, densityRatchet.mjs 문서 참조). 성능 요구는
// bench/idempotencyScan_bench.mjs로 옮겼다: 스캔 직전 같은 프로세스에서 측정한
// 참조 워크로드(calib) 대비 배율로 판정해, 워크트리가 얼마나 붐비든 "이 실행 자체가
// 기준보다 몇 배 느린가"만 본다 — 알고리즘이 실제로 비선형 회귀했을 때는 calib도
// 같이 비례해 커지지 않으므로 여전히 잡힌다. `npm run bench:idempotency-scan
// --workspace=packages/api-client`로 실행(CI에서는 density_bench와 동일하게
// 별도 label 단계).
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
