import { expect, test } from "@playwright/test";
import { fieldControl } from "./support/fieldControl";
import { mockBackend } from "./support/mockBackend";

// H-7b 깊이 보강(D1->D2): 주문 제출 / quick 백테스트 / 지표 목록 3개 화면의
// 실패 경로(ErrorMessage 배너)를 단언하고, 진입~핵심 요소 표시 시간 예산과
// 응답 변조 시 단언이 실제로 실패하는지(gate red)를 확인한다. 고정 sleep 없음.
// 앱 결함 보고: @aios/ui-web Alert(Feedback.tsx)가 role="alert"를 달지 않아 주문/백테스트/
// 지표 화면 배너는 role 조회로 잡을 수 없다(프로덕션 수정 금지 — 보고만). 그 3곳은
// ErrorMessage의 "지원코드: <trace_id>" 문구로 특정하고, role="alert"를 실제로 다는
// TcaPage 폼 검증 오류는 getByRole("alert")로 단언한다.
const banner = (page: import("@playwright/test").Page) => page.getByText("지원코드: e2e-h7b-depth");

const FAULT = (status: number, message: string) => ({
  status,
  body: {
    error_code: status === 409 ? "CONFLICT" : "INTERNAL_ERROR",
    message,
    details: {},
    trace_id: "e2e-h7b-depth",
    retry_after_seconds: null,
  },
});

async function submitOrder(page: import("@playwright/test").Page, strategy: string) {
  await page.goto("/executions");
  await fieldControl(page, "전략 ID").fill(strategy);
  await fieldControl(page, "버전").fill("1.0.0");
  await fieldControl(page, "배분 자본(USDT)").fill("500");
  await page.getByRole("button", { name: "실행 생성" }).click();
}

test.describe("H-7b depth", () => {
  test("[실패 주입] 주문 제출 409면 오류 배너가 뜨고 목록에 카드가 생기지 않는다", async ({ page }) => {
    await mockBackend(page, { createExecutionResponse: FAULT(409, "이미 존재하는 실행입니다.") });
    await submitOrder(page, "h7b-conflict");
    await expect(banner(page)).toBeVisible();
    // 실패한 제출은 목록 카드로 반영되지 않는다(폼 입력값 외에 텍스트 노출 없음).
    await expect(page.getByRole("article").filter({ hasText: "h7b-conflict" })).toHaveCount(0);
    await expect(fieldControl(page, "전략 ID")).toHaveValue("h7b-conflict");
  });

  test("[실패 주입] quick 백테스트 500이면 오류 배너가 뜨고 결과 요약이 없다", async ({ page }) => {
    await mockBackend(page, { quickBacktestResponse: FAULT(500, "백테스트 엔진 오류") });
    await page.goto("/chart?instrument_id=BTCUSDT");
    const run = page.getByTestId("backtest-run");
    await expect(run).toBeEnabled();
    await run.click();
    await expect(banner(page)).toBeVisible();
    await expect(page.getByTestId("backtest-summary")).toHaveCount(0);
  });

  test("[실패 주입] 지표 목록 조회 500이면 오류 배너가 뜨고 지표 옵션이 없다", async ({ page }) => {
    await mockBackend(page);
    await page.route("http://localhost:8000/v1/indicators**", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify(FAULT(500, "지표 카탈로그 조회 실패").body),
      }),
    );
    await page.goto("/chart?instrument_id=BTCUSDT");
    await page.getByRole("button", { name: "지표 선택 (0)" }).click();
    // useInfiniteQuery 기본 재시도 백오프로 배너까지 ~8s 걸려 기본 10s 예산이 빠듯하다.
    await expect(banner(page)).toBeVisible({ timeout: 40_000 });
    await expect(page.locator("#indicator-option-sma")).toHaveCount(0);
  });

  test("차트 진입~백테스트 실행 버튼 활성화가 예산 안에 끝난다", async ({ page }) => {
    await mockBackend(page);
    const start = performance.now();
    await page.goto("/chart?instrument_id=BTCUSDT");
    await expect(page.getByTestId("backtest-run")).toBeEnabled();
    const elapsed = performance.now() - start;
    // 로컬 실측 ~1s(preview 빌드 서빙, 모의 백엔드). 예산 60s는 실측의 30배 이상 —
    // 느린 CI 러너·4 worker 경합에도 깨지지 않게 잡았다(회귀 감지 목적의 상한).
    expect(elapsed).toBeLessThan(60_000);
  });

  // gate red: 정상 경로 단언(성공 결과 표시)이 응답 변조(실패 주입) 입력에서는 실제로 throw 해야
  // 게이트가 적색이 된다. 세 화면 모두 같은 단언을 rejects.toThrow로 뒤집어 확인한다.
  test("gate red: 변조된 quick 백테스트 응답이면 결과 요약 단언이 실패한다", async ({ page }) => {
    await mockBackend(page, { quickBacktestResponse: FAULT(500, "변조 응답") });
    await page.goto("/chart?instrument_id=BTCUSDT");
    await expect(page.getByTestId("backtest-run")).toBeEnabled();
    await page.getByTestId("backtest-run").click();
    await expect(banner(page)).toBeVisible();
    await expect(async () => {
      await expect(page.getByTestId("backtest-summary")).toBeVisible({ timeout: 1_000 });
    }).rejects.toThrow();
  });

  test("gate red: 변조된 주문 응답이면 목록 카드 단언이 실패한다", async ({ page }) => {
    await mockBackend(page, { createExecutionResponse: FAULT(409, "변조 응답") });
    await submitOrder(page, "h7b-tamper");
    await expect(banner(page)).toBeVisible();
    await expect(async () => {
      await expect(fieldControl(page, "전략 ID")).toHaveValue("", { timeout: 1_000 });
    }).rejects.toThrow();
  });

  test("gate red: 변조된 지표 목록 응답이면 지표 옵션 단언이 실패한다", async ({ page }) => {
    await mockBackend(page);
    await page.route("http://localhost:8000/v1/indicators**", (route) =>
      route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify(FAULT(500, "x").body) }),
    );
    await page.goto("/chart?instrument_id=BTCUSDT");
    await page.getByRole("button", { name: "지표 선택 (0)" }).click();
    // useInfiniteQuery 기본 재시도 백오프로 배너까지 ~8s 걸려 기본 10s 예산이 빠듯하다.
    await expect(banner(page)).toBeVisible({ timeout: 40_000 });
    await expect(async () => {
      await expect(page.locator("#indicator-option-sma")).toBeVisible({ timeout: 1_000 });
    }).rejects.toThrow();
  });

  // TCA 미계산(404) -> 재계산 폼 -> 잘못된 JSON 제출. role="alert"가 표면화되고 계산 요청은 나가지 않는다.
  async function openTcaForm(page: import("@playwright/test").Page, computeCalls: string[]) {
    await mockBackend(page);
    await page.route("http://localhost:8000/v1/foundation/ems/tca/**", (route) => {
      if (route.request().method() === "POST") computeCalls.push(route.request().url());
      return route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify(FAULT(404, "없음").body),
      });
    });
    await page.goto("/executions/p1/tca");
    await page.getByRole("button", { name: "TCA 계산 시작" }).click();
    await page.locator("#tca-compute-fills").fill("{not json");
    await page.getByRole("button", { name: "계산", exact: true }).click();
  }

  test("[실패 주입] TCA 재계산 폼에 깨진 JSON을 제출하면 alert가 뜨고 계산 요청이 나가지 않는다", async ({ page }) => {
    const computeCalls: string[] = [];
    await openTcaForm(page, computeCalls);
    await expect(page.getByRole("alert")).toHaveText("JSON 형식이 올바르지 않습니다.");
    expect(computeCalls).toHaveLength(0);
  });

  test("gate red: 올바른 JSON이어도 alert가 남는다고 단언하면 실제로 실패한다", async ({ page }) => {
    const computeCalls: string[] = [];
    await openTcaForm(page, computeCalls);
    await expect(page.getByRole("alert")).toBeVisible();
    await page.locator("#tca-compute-fills").fill("[]");
    await page.getByRole("button", { name: "계산", exact: true }).click();
    // 다른 오류 문구(빈 배열)로 바뀐다 — 이전 문구(깨진 JSON) 단언은 변조 입력에서 throw 해야 한다.
    await expect(async () => {
      await expect(page.getByRole("alert")).toHaveText("JSON 형식이 올바르지 않습니다.", { timeout: 1_000 });
    }).rejects.toThrow();
    await expect(page.getByRole("alert")).toHaveText("체결 내역과 가격 봉은 비어 있을 수 없습니다.");
  });
});
