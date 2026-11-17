import { expect, test } from "@playwright/test";
import { fieldControl } from "./support/fieldControl";
import { mockBackend } from "./support/mockBackend";

// H-7b 깊이 보강(DEEPEN-DEPTH) — 기존 스모크 3건(order-submission·backtest-run·
// chart-indicator-overlay)에 없는 실패 경로·성능·게이트 적색 재현을 한 파일에
// 모은다. 기존 스펙과 support/는 건드리지 않는다. 모든 요청은 mockBackend의
// page.route로 가로채며 실 백엔드를 부르지 않는다.
//
// 주의: 현재 ui-web Alert(packages/ui-web/src/Feedback.tsx)는 role="alert"를
// 달지 않는다. 그래서 오류 표면화는 getByRole("alert") 대신 매핑된 오류 문구
// 텍스트(getByText)로 단언한다 — 앱 코드를 고치지 않기 위한 선택이다.

const API_BASE = "http://localhost:8000";
// 로컬 실측 480~570ms(3회). 예산 20s는 실측의 35배 이상 — 느린 CI 러너에서도 깨지지 않게.
const PERF_BUDGET_MS = 20_000;

function faultBody(message: string) {
  return {
    error_code: null,
    message,
    details: {},
    trace_id: "e2e-depth-fault",
    retry_after_seconds: null,
  };
}

test.describe("H-7b 깊이 보강", () => {
  test("[실패 주입] 주문 제출 409 응답이면 오류를 표면화하고 목록에 반영하지 않는다", async ({ page }) => {
    await mockBackend(page, {
      createExecutionResponse: { status: 409, body: faultBody("e2e 주문 충돌 주입") },
    });
    await page.goto("/executions");

    await fieldControl(page, "전략 ID").fill("e2e-conflict-strategy");
    await fieldControl(page, "버전").fill("1.0.0");
    await fieldControl(page, "배분 자본(USDT)").fill("500");
    await page.getByRole("button", { name: "실행 생성" }).click();

    await expect(page.getByText("e2e 주문 충돌 주입")).toBeVisible();
    await expect(page.getByText("e2e-conflict-strategy")).toHaveCount(0);
    await expect(fieldControl(page, "전략 ID")).toHaveValue("e2e-conflict-strategy");
  });

  test("[실패 주입] 빠른 백테스트 500 응답이면 오류를 보여주고 결과 요약을 그리지 않는다", async ({ page }) => {
    await mockBackend(page, {
      quickBacktestResponse: { status: 500, body: faultBody("e2e 백테스트 서버 오류 주입") },
    });
    await page.goto("/chart?instrument_id=BTCUSDT");

    await expect(page.getByTestId("chart-instrument-id")).toHaveText("BTCUSDT");
    await page.getByTestId("backtest-run").click();

    await expect(page.getByText("e2e 백테스트 서버 오류 주입")).toBeVisible();
    await expect(page.getByTestId("backtest-summary")).toHaveCount(0);
  });

  test("[실패 주입] 지표 목록 조회가 500이면 오류를 보여주고 지표 옵션을 노출하지 않는다", async ({ page }) => {
    await mockBackend(page);
    // mockBackend 뒤에 등록한 라우트가 먼저 매칭된다(Playwright 후입선출) —
    // support/mockBackend.ts를 바꾸지 않고 이 테스트만 실패 응답으로 덮는다.
    await page.route(
      (url) => url.origin === API_BASE && url.pathname === "/v1/indicators",
      (route) =>
        route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify(faultBody("e2e 지표 목록 조회 실패 주입")),
        }),
    );
    await page.goto("/chart?instrument_id=BTCUSDT");
    await expect(page.getByTestId("chart-instrument-id")).toHaveText("BTCUSDT");

    await page.getByRole("button", { name: "지표 선택 (0)" }).click();

    // react-query 기본 재시도(3회, 지수 백오프 약 7초)가 끝난 뒤에야 오류가 그려진다.
    await expect(page.getByText("e2e 지표 목록 조회 실패 주입")).toBeVisible({ timeout: 20_000 });
    await expect(page.locator("#indicator-option-sma")).toHaveCount(0);
  });

  test("성능: /executions 진입 후 핵심 헤딩 표시까지 예산 안에 끝난다", async ({ page }) => {
    await mockBackend(page);
    const started = performance.now();
    await page.goto("/executions");
    await expect(page.getByRole("heading", { name: "실행 제어판" })).toBeVisible();
    const elapsed = performance.now() - started;

    // 예산(PERF_BUDGET_MS)은 위 상수 주석의 실측 기준이다. 수 배 이상 지연 회귀만 잡는다.
    expect(elapsed).toBeLessThan(PERF_BUDGET_MS);
  });

  test("gate red: 응답을 변조하면(최종 자본 불일치) 결과 요약 단언이 실패한다", async ({ page }) => {
    // 정상 응답(10500)을 99999로 변조한 입력. 같은 요약 단언이 여기서 실패해야 단언이 실제로 살아 있다.
    await mockBackend(page, {
      quickBacktestResponse: {
        status: 200,
        body: {
          data: {
            fills: [],
            equity_curve: ["99999", "99999"],
            final_equity: "99999",
            cash: "99999",
            position_quantity: "0",
            funding_cost: "0",
            borrow_cost: "0",
            bars: 24,
            expired_orders: 0,
            warnings: [],
          },
          meta: { trace_id: "e2e-tampered", as_of: new Date().toISOString(), page: null },
        },
      },
    });
    await page.goto("/chart?instrument_id=BTCUSDT");
    await page.getByTestId("backtest-run").click();

    const summary = page.getByTestId("backtest-summary");
    await expect(summary).toBeVisible();
    await expect(async () => {
      await expect(summary).toContainText("10500", { timeout: 2_000 });
    }).rejects.toThrow();
  });
});
