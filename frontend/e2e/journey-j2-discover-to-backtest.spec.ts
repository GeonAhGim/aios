import { expect, test, type Page, type Route } from "@playwright/test";
import { mockBackend } from "./support/mockBackend";

// task-6665 (J2): docs/specs/UX_JOURNEYS.md J2 단계표(스크리닝→차트·지표→전략
// 빌더/스크립트→즉시 백테스트→결과 해석)를 그대로 따라간다. mockBackend가 덮지
// 않는 strategy-builder/scripts.compile 경로는 이 파일에서 page.route로 얹는다 —
// Playwright는 나중에 등록한 route를 먼저 검사하므로 mockBackend(page) 호출 뒤에
// 추가해야 mockBackend의 폴백(catch-all)과 충돌하지 않는다.
//
// 갭 노트(문서 UX_JOURNEYS.md의 "있음" 표기와 실제 코드가 어긋나는 지점, apiRoutes.ts
// 기준): 1단계 screener.run, 7단계 researchData.search·researchData.sources.list는
// implemented:false 유령 경로다. 각 클라이언트가 fetch 이전에
// *RouteNotImplementedError를 던지므로 page.route 목으로도 성공 응답을 만들 수
// 없다 — 이 단계들은 test.fixme로 남기고 우회하지 않는다. 6단계 backtests.sweep은
// task-7774(BT-18)로 implemented:true가 됐지만, 이 저널니 안에는 여전히
// /backtest/sweep-results로 들어가는 CTA가 없어(location.state 진입만 가능) 별도
// 사유로 fixme다.
// G-3(문서 §2 J2 5→6단계 단절, 스크립트 컴파일이 백테스트로 자동 연결되지 않음)도
// ScriptEditorPage.tsx 전체를 읽어 재확인했다. "즉시 백테스트" 단계는 실제로 동작하는
// /chart의 quick-backtest(POST /v1/backtests/quick, backtest-run.spec.ts와 동일 패턴)로
// 충족한다 — 유령 경로인 sweep-results로 우회 성공을 흉내내지 않는다.

const API_BASE = "http://localhost:8000";

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function envelope(data: unknown) {
  return { data, meta: { trace_id: "e2e-trace", as_of: new Date().toISOString(), page: null } };
}

async function mockStrategyBuilder(page: Page) {
  await page.route(`${API_BASE}/strategy-builder/indicators`, (route) =>
    json(route, 200, { indicators: ["RSI", "SMA", "EMA"] }),
  );
  await page.route(`${API_BASE}/strategy-builder/candles**`, (route) =>
    json(route, 200, [
      {
        open_time: "2026-01-01T00:00:00Z",
        close_time: "2026-01-01T00:59:59Z",
        open: "100",
        high: "110",
        low: "90",
        close: "105",
        volume: "10",
      },
    ]),
  );
  await page.route(`${API_BASE}/strategy-builder/preview`, (route) =>
    json(route, 200, {
      signal_indices: [3, 8],
      signal_times: ["2026-01-01T03:00:00Z", "2026-01-01T08:00:00Z"],
      disclaimer: "과거 데이터 기반 참고용 신호입니다.",
      message: null,
    }),
  );
  await page.route(`${API_BASE}/strategy-builder/strategies`, (route) =>
    json(route, 200, {
      strategy_id: "e2e-momentum-strategy",
      version: "1.0.0",
      status: "draft",
      fsm_definition: {},
    }),
  );
}

async function mockScriptCompile(page: Page, override?: { status: number; body: unknown }) {
  await page.route(`${API_BASE}/v1/scripts/compile`, (route) => {
    if (override) return json(route, override.status, override.body);
    return json(
      route,
      200,
      envelope({
        script_hash: "e2e-hash-0000000000000000000000000000000000000000000000000000000000",
        grammar_version: "aios-script-1",
        ir_version: "aios-ir-1",
        registry_version: "reg-v1",
        ir_sha256: "e2e-sha-000000000000000000000000000000000000000000000000000000000",
        instr_count: 5,
        resources: { series_count: 1, lookback_total: 14, op_count: 3, call_count: 1, call_depth: 1, plot_count: 1 },
        elapsed_ms: 12,
      }),
    );
  });
}

test.describe("J2 여정: 스크리너→차트·지표→전략 빌더/스크립트→즉시 백테스트→결과 해석", () => {
  test("1단계 [유령경로] 스크리너 실행은 실행 API 미구현 오류를 그대로 보여준다", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/screener");

    await page.getByTestId("screener-universe").fill("KRX");
    await page.getByTestId("screener-filter-0-field").fill("rsi_14");
    await page.getByTestId("screener-filter-0-value").fill("30");
    await page.getByTestId("screener-run").click();

    await expect(page.getByText("스크리너 실행 API가 아직 제공되지 않습니다.")).toBeVisible();
  });

  // 갭: screener.run이 유령 경로라 결과 행이 생기지 않으므로 "결과 행 클릭 → /chart
  // 이동"은 이 상태에서 재현할 수 없다. apiRoutes.ts에서 screener.run이
  // implemented:true로 바뀌기 전까지는 우회 없이 fixme로 남긴다.
  test.fixme("1→2단계 [유령경로] 스크리너 결과 행을 클릭하면 /chart로 이동한다", async ({ page }) => {
    // 갭: screener.run이 implemented:false라 ScreenerClient.runScreen()이 fetch 전에
    // ScreenerRouteNotImplementedError를 던진다 — 결과 행 자체가 생기지 않아 이동 경로를
    // 재현할 수 없다. apiRoutes.ts에서 실제 구현되면 이 테스트를 채운다.
    await mockBackend(page);
    await page.goto("/screener");
  });

  test("2→3단계 심볼/캔들 조회 후 차트·지표 화면에서 상태가 표시된다", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/chart?instrument_id=BTCUSDT");

    await expect(page.getByTestId("chart-instrument-id")).toContainText("BTCUSDT");
  });

  test("4단계 전략 빌더에서 조건을 미리보고 전략을 저장한다", async ({ page }) => {
    await mockBackend(page);
    await mockStrategyBuilder(page);
    await page.goto("/strategy-builder");

    await page.getByPlaceholder("my-rsi-strategy").fill("e2e-momentum-strategy");
    await page.getByRole("button", { name: "진입 조건 미리보기" }).click();
    await expect(page.getByText(/신호 발생 시점/)).toBeVisible();

    await page.getByRole("button", { name: "전략 저장" }).click();
    await expect(page.getByText(/전략이 저장됐습니다/)).toBeVisible();
  });

  // 실패주입(negative): 전략 ID를 비워둔 채 저장을 시도하면 서버 호출 없이
  // 클라이언트 검증 문구만 뜬다 — StrategyBuilderPage.test.tsx가 이미 확인한
  // 문구를 여정 경로로도 재현한다.
  test("[실패 주입] 전략 ID 없이 저장하면 클라이언트 검증 문구가 뜨고 저장 API는 호출되지 않는다", async ({
    page,
  }) => {
    await mockBackend(page);
    let createCalled = false;
    await page.route(`${API_BASE}/strategy-builder/strategies`, (route) => {
      createCalled = true;
      return json(route, 200, { strategy_id: "should-not-be-called", version: "1.0.0", status: "draft" });
    });
    await mockStrategyBuilder(page);
    await page.goto("/strategy-builder");

    await page.getByRole("button", { name: "전략 저장" }).click();

    await expect(page.getByText("전략 ID를 입력해주세요.")).toBeVisible();
    expect(createCalled).toBe(false);
  });

  test("5단계 스크립트 편집기에서 컴파일 결과를 확인한다", async ({ page }) => {
    await mockBackend(page);
    await mockScriptCompile(page);
    await page.goto("/scripts/editor");

    await page.getByRole("button", { name: "컴파일" }).click();

    await expect(page.getByTestId("compile-preview-hash")).toContainText("스크립트 해시:");
  });

  // G-3(UX_JOURNEYS.md §2 J2 5→6단계 단절, task-7785로 해소): 컴파일 성공 시 CTA
  // ("차트에서 백테스트")가 나타나고, 누르면 scriptHash를 실어 /chart(quick-backtest가
  // 실제로 동작하는 화면, 6단계 테스트와 동일 경로)로 이동한다.
  test("5→6단계 [G-3 해소] 스크립트 컴파일 성공 시 CTA로 즉시 백테스트 화면에 연결된다", async ({ page }) => {
    await mockBackend(page);
    await mockScriptCompile(page);
    await page.goto("/scripts/editor");

    await page.getByRole("button", { name: "컴파일" }).click();
    await expect(page.getByTestId("compile-preview-hash")).toBeVisible();

    await page.getByTestId("compile-open-chart").click();

    await expect(page).toHaveURL(/\/chart\?script_hash=e2e-hash-0+$/);
  });

  // 실패주입(negative): 컴파일이 실패하면 CTA 자체가 뜨지 않아 사용자가 잘못된
  // 컴파일 결과로 백테스트 화면에 진입할 수 없다.
  test("negative: 컴파일 실패 시 백테스트 CTA가 뜨지 않는다", async ({ page }) => {
    await mockBackend(page);
    await mockScriptCompile(page, {
      status: 400,
      body: {
        error_code: "VALIDATION_INVALID_FIELD",
        message: "SCRIPT_SYNTAX: unexpected end of input",
        details: { code: "SCRIPT_SYNTAX", line: 1, col: 1 },
        trace_id: "e2e-trace-400",
      },
    });
    await page.goto("/scripts/editor");

    await page.getByRole("button", { name: "컴파일" }).click();

    await expect(page.getByTestId("script-editor-marker-0")).toBeVisible();
    await expect(page.getByTestId("compile-open-chart")).toHaveCount(0);
  });

  test("6단계 즉시 백테스트: 컴파일된 전략을 /chart의 quick-backtest로 바로 실행한다", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/chart?instrument_id=BTCUSDT");

    await page.getByTestId("backtest-run").click();

    await expect(page.getByTestId("backtest-summary")).toContainText("10500");
  });

  // 갭(task-7774 BT-18로 절반 해소): backtests.sweep은 이제 implemented:true고
  // 서버 엔드포인트(POST /v1/backtests/sweep)도 배선됐다 — 더 이상 유령 경로가
  // 아니다(SweepResultsPage.test.tsx의 단위 테스트가 실제 응답 렌더링을 이미
  // 검증한다). 남은 갭은 이 저널니 안에 /backtest/sweep-results로 들어가는
  // 진입점(스윕 구성→실행 CTA)이 아직 없다는 것 — SweepResultsPage는
  // react-router location.state.sweepRequest로만 요청을 받는데, Playwright의
  // page.goto는 그 state를 주입할 수 없다. CTA 배선은 이 리프(API 계층만)의
  // 범위 밖이라 우회하지 않고 fixme로 남긴다.
  test.fixme("6단계 [유령경로 절반 해소, CTA 미배선] 파라미터 스윕 결과 화면이 스윕 실행 결과를 보여준다", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/backtest/sweep-results");
  });

  // 갭: researchData.search·researchData.sources.list 모두 implemented:false
  // 유령 경로다. ResearchPage.tsx는 마운트 시 sourcesQuery를 무조건 실행하므로
  // 검색 버튼을 누르기도 전에 소스 카드가 이미 미구현 오류 배너를 보여준다.
  test.fixme("7단계 [유령경로] 리서치 검색으로 결과를 해석한다", async ({ page }) => {
    // 갭: researchData.search·researchData.sources.list 모두 implemented:false —
    // ResearchDataClient가 fetch 전에 ResearchDataRouteNotImplementedError를 던진다.
    // ResearchPage.tsx는 마운트 시 sourcesQuery를 무조건 실행하므로 검색 버튼을
    // 누르기도 전에 소스 카드가 이미 미구현 오류 배너를 보여준다 — 목으로도 재현 불가.
    await mockBackend(page);
    await page.goto("/research");
  });
});
